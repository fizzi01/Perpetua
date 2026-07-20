"""Daemon service exposing a command socket to manage Client/Server lifecycle."""


#  Perpetua - open-source and cross-platform KVM software.
#  Copyright (c) 2026 Federico Izzi.
#
#  This program is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program.  If not, see <https://www.gnu.org/licenses/>.
#

import asyncio
import datetime
import errno
import msgspec
import os
import time
import threading

from os import path
import signal
import socket
import sys
from typing import Optional, Dict, Any, Callable
from enum import StrEnum

from config import ApplicationConfig, ServerConfig, ClientConfig
from service.client import Client
from service.server import Server, ServerStartError
from utils import UIDGenerator, BackgroundTasks
from utils.logging import Logger, get_logger
from utils.cli import DaemonArguments
from utils.permissions import PermissionChecker
from utils.runtime import (
    env_endpoint_override,
    endpoint_to_socket_path,
    format_tcp_endpoint,
    format_unix_endpoint,
    remove_endpoint,
    write_endpoint,
)
from event.notification import (
    NotificationManager,
    NotificationEvent,
    OtpGeneratedEvent,
    InfoEvent,
    ErrorEvent,
)


IS_WINDOWS = sys.platform in ("win32", "cygwin")


class DaemonException(Exception):
    """Base exception for daemon errors."""

    pass


class DaemonAlreadyRunningException(DaemonException):
    """Raised when daemon socket/port is already in use by a live daemon."""

    pass


class DaemonPortOccupiedException(DaemonException):
    """Raised when the TCP port is occupied by another process."""

    pass


class DaemonCommand(StrEnum):
    """Available daemon commands."""

    # Service control
    SERVICE_CHOICE = "service_choice"
    START_SERVER = "start_server"
    STOP_SERVER = "stop_server"
    START_CLIENT = "start_client"
    STOP_CLIENT = "stop_client"

    # Status queries
    STATUS = "status"
    SERVER_STATUS = "server_status"
    CLIENT_STATUS = "client_status"

    # Configuration management
    GET_SERVER_CONFIG = "get_server_config"
    SET_SERVER_CONFIG = "set_server_config"
    GET_CLIENT_CONFIG = "get_client_config"
    SET_CLIENT_CONFIG = "set_client_config"
    SAVE_CONFIG = "save_config"
    RELOAD_CONFIG = "reload_config"

    # Stream management
    ENABLE_STREAM = "enable_stream"
    DISABLE_STREAM = "disable_stream"
    GET_STREAMS = "get_streams"

    # Client management (server only)
    ADD_CLIENT = "add_client"
    REMOVE_CLIENT = "remove_client"
    EDIT_CLIENT = "edit_client"
    LIST_CLIENTS = "list_clients"
    APPROVE_CLIENT = "approve_client"
    DENY_CLIENT = "deny_client"
    LIST_PENDING_APPROVALS = "list_pending_approvals"
    # Multi-monitor layout (server only)
    SET_CLIENT_LAYOUT = "set_client_layout"

    # SSL/Certificate management
    ENABLE_SSL = "enable_ssl"
    DISABLE_SSL = "disable_ssl"
    SHARE_CERTIFICATE = "share_certificate"
    RECEIVE_CERTIFICATE = "receive_certificate"
    SET_OTP = "set_otp"
    REQUEST_PAIRING = "request_pairing"

    # Server selection (client)
    CHECK_SERVER_CHOICE_NEEDED = "check_server_choice_needed"
    GET_FOUND_SERVERS = "get_found_servers"
    CHOOSE_SERVER = "choose_server"
    CHECK_OTP_NEEDED = "check_otp_needed"

    # Service discovery
    DISCOVER_SERVICES = "discover_services"

    # Daemon control
    SHUTDOWN = "shutdown"
    PING = "ping"

    # Autostart-at-login (cross-platform)
    GET_AUTOSTART = "get_autostart"
    SET_AUTOSTART = "set_autostart"

    def __init__(self, params: Optional[Dict[str, Any]] = None):
        self._params = params if params is not None else {}

    @property
    def params(self) -> Dict[str, Any]:
        return self._params

    @staticmethod
    def new(command: str, **kwargs) -> "DaemonCommand":
        cmd = DaemonCommand(command)
        cmd._params = kwargs
        return cmd

    def to_dict(self) -> Dict[str, Any]:
        return {"command": self.value, "params": self._params}


class RunningState:
    def __init__(self, service: str, is_running: bool):
        self.service = service
        self.is_running = is_running
        self.start_datetime = None

    def start(self):
        self.is_running = True
        self.start_datetime = datetime.datetime.now()

    def stop(self):
        self.is_running = False
        self.start_datetime = None

    def get_timestamp(self) -> Optional[str]:
        if self.start_datetime:
            return self.start_datetime.isoformat()
        return None


class CommandHandler:
    """Decorator + registry for daemon command handlers."""

    _handlers: Dict[str, Callable] = {}

    @classmethod
    def register(cls, command: str):
        def decorator(func: Callable) -> Callable:
            cls._handlers[command] = func
            return func

        return decorator

    @classmethod
    def get_handlers(cls, instance: Optional[Any] = None) -> Dict[str, Callable]:
        """Return registered handlers, optionally bound to ``instance``."""
        if instance is None:
            return cls._handlers.copy()

        bound_handlers = {}
        for command, func in cls._handlers.items():
            bound_handlers[command] = func.__get__(instance, instance.__class__)  # ty:ignore[unresolved-attribute]
        cls.clear()  # Avoid duplicate registrations on re-instantiation.
        return bound_handlers

    @classmethod
    def clear(cls):
        cls._handlers.clear()


class Daemon:
    """Command-socket daemon controlling the Client/Server services.

    Uses a Unix socket on Linux/macOS and a localhost TCP socket on
    Windows (named pipes don't play well with asyncio).
    """

    if IS_WINDOWS:
        DEFAULT_SOCKET_PATH = f"127.0.0.1:{ApplicationConfig.DEFAULT_DAEMON_PORT}"
    else:
        # Unix socket lives under $XDG_RUNTIME_DIR on Linux (tmpfs, per-session)
        # and falls back to the state dir if that's not available; macOS keeps
        # the historical location under ~/Library/Caches/Perpetua.
        DEFAULT_SOCKET_PATH: str = path.join(
            ApplicationConfig.get_runtime_path(),
            ApplicationConfig.DEFAULT_UNIX_SOCK_NAME,
        )

    MAX_CONNECTIONS = 1
    BUFFER_SIZE = 16384
    # Cap concurrent commands so an abusive client can't spam the
    # fire-and-forget task set unbounded.
    MAX_CONCURRENT_COMMANDS = 16
    # On Windows, walk forward this many adjacent ports if the preferred
    # one is busy. The actual bound port lives in the runtime endpoint
    # file so the GUI keeps finding us.
    TCP_FALLBACK_PORT_RANGE = 10

    # Watchdog window before ``delayed_exit`` escalates after stop().
    # Long enough for bg-task drain + mDNS de-announce + config save.
    DELAYED_EXIT_TIMEOUT = 30.0
    # Env var opt-in for the legacy hard-exit fallback. Off by default
    # so a hung shutdown surfaces in diagnostics instead of being masked.
    FORCE_EXIT_ENV_VAR = "PERPETUA_DAEMON_FORCE_EXIT"

    _encoder = msgspec.json.Encoder()
    _decoder = msgspec.json.Decoder()

    def __init__(
        self,
        socket_path: Optional[str] = None,
        app_config: Optional[ApplicationConfig] = None,
        auto_load_config: bool = True,
    ):
        # IPC endpoint precedence: explicit arg -> PERPETUA_DAEMON_ENDPOINT
        # env var -> platform default.
        env_ep = env_endpoint_override()
        if socket_path:
            self.socket_path = socket_path
        elif env_ep:
            self.socket_path = endpoint_to_socket_path(env_ep)
        else:
            self.socket_path = self.DEFAULT_SOCKET_PATH
        self.app_config = app_config or ApplicationConfig()
        self.auto_load_config = auto_load_config
        # Endpoint URL we actually bound; persisted so the GUI can find
        # us even after a port fallback.
        self._endpoint_url: Optional[str] = None

        self._logger = get_logger(
            self.__class__.__name__,
            level=Logger.INFO,
            is_root=True,
            log_file=self.app_config.get_default_log_file(),
        )

        self._server: Optional[Server] = None
        self._client: Optional[Client] = None
        self._state: Dict[str, RunningState] = {
            "server": RunningState("server", False),
            "client": RunningState("client", False),
        }

        self._server_config: Optional[ServerConfig] = None
        self._client_config: Optional[ClientConfig] = None

        self._notification_manager = NotificationManager(
            callback=self._send_notification
        )

        self._running = False
        self._shutdown_event = asyncio.Event()
        self._socket_server: Optional[asyncio.AbstractServer] = None
        self._permission_watchdog_task: Optional[asyncio.Task] = None

        # Only one instance may connect at a time.
        self._connected_client_reader: Optional[asyncio.StreamReader] = None
        self._connected_client_writer: Optional[asyncio.StreamWriter] = None
        self._client_connection_lock = asyncio.Lock()

        self._command_handlers: Dict[str, Callable] = CommandHandler.get_handlers(self)

        self._bg_tasks = BackgroundTasks()
        self._command_semaphore = asyncio.Semaphore(self.MAX_CONCURRENT_COMMANDS)

        # Signal handlers register in ``start()`` once a loop is running;
        # ``__init__`` would have to rely on the deprecated
        # ``asyncio.get_event_loop()`` outside a running loop.
        self._shutdown_calls = 0

    def _setup_signal_handlers(self):
        """Install SIGTERM/SIGINT handlers. Must be called from a running loop."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(
                    sig,
                    lambda: self._bg_tasks.spawn(
                        self._signal_shutdown(), name="signal_shutdown"
                    ),
                )
            except NotImplementedError:
                # Windows asyncio does not support add_signal_handler.
                pass

    async def _permission_watchdog(self, interval: float = 5.0):
        """Trigger graceful shutdown if required permissions are revoked.

        Runs the check in a thread executor so a stuck system call can't
        block the event loop.
        """
        checker = PermissionChecker(log=False)
        try:
            loop = asyncio.get_running_loop()
        except Exception:
            self._logger.error("Permission watchdog failed to get event loop")
            return
        self._logger.info("Permission watchdog started", interval=interval)

        # Cap each check so a stuck syscall can't hold the watchdog (or
        # the default executor's loop slot) hostage.
        check_timeout = max(interval, 5.0)
        max_retry = 3
        try:
            while self._running:
                await asyncio.sleep(interval)
                try:
                    result = await asyncio.wait_for(
                        loop.run_in_executor(None, checker.check_accessibility_live),
                        timeout=check_timeout,
                    )
                    if result.is_denied:
                        self._logger.error(
                            "Accessibility permission revoked at runtime, shutting down"
                        )
                        await self._notification_manager.notify_error(
                            error="Accessibility permission was revoked. "
                            "Shutting down to prevent input lock.",
                            context="permission_watchdog",
                        )
                        await self.stop()
                        return
                except asyncio.TimeoutError:
                    self._logger.warning(
                        "Permission watchdog check timed out",
                        timeout=check_timeout,
                    )
                    max_retry -= 1
                    if max_retry <= 0:
                        self._logger.error(
                            "Permission watchdog check failed repeatedly, shutting down"
                        )
                        await self._notification_manager.notify_error(
                            error="Permission watchdog check failed repeatedly. "
                            "Shutting down to prevent potential input lock.",
                            context="permission_watchdog",
                        )
                        await self.stop()
                        return
                except Exception as e:
                    self._logger.warning(
                        "Permission watchdog check failed", error=str(e)
                    )
        except asyncio.CancelledError:
            pass

    async def _signal_shutdown(self):
        self._logger.info("Received shutdown signal")
        await self.stop()
        self._shutdown_calls += 1

        if self._shutdown_calls >= 3:
            self._logger.warning(
                "Shutdown signal received multiple times, forcing exit",
                count=self._shutdown_calls,
            )
            os._exit(0)

    def _pre_configure(self):
        """Backfill missing hostname/UID on Client/Server configs before start."""
        if self._client_config:
            if (
                not self._client_config.get_hostname()
                or self._client_config.get_hostname() == ""
            ):
                try:
                    key = self._client_config.get_hostname()
                    if not key or key == "":
                        key = socket.gethostname()
                        self._logger.warning(
                            "Client configuration missing hostname, setting new one",
                            new_hostname=key,
                        )
                        self._client_config.set_hostname(key)
                except Exception as e:
                    self._logger.error("Preconfiguration error", error=str(e))
            if not self._client_config.get_uid():
                key = self._client_config.get_hostname()
                if key and key != "":
                    try:
                        new_uid = UIDGenerator.generate_uid(key)
                        self._client_config.set_uid(new_uid)
                        self._logger.info(
                            "Generated new client UID", client_uid=new_uid
                        )
                    except Exception as e:
                        self._logger.error("Preconfiguration error", error=str(e))

        if self._server_config:  # UID will be generated by service discovery
            if not self._server_config.host:
                try:
                    hostname = socket.gethostname()
                    self._server_config.host = hostname
                    self._logger.info(
                        "Server configuration missing host, setting to system hostname",
                        host=hostname,
                    )
                except Exception as e:
                    self._logger.error("Preconfiguration error", error=str(e))

    async def start(self, service: Optional[str] = None) -> bool:
        """Start the daemon and command socket server."""
        if self._running:
            self._logger.warning("Daemon already running")
            return False

        self._logger.info("Starting Daemon...")

        self._server_config = ServerConfig(self.app_config)
        self._client_config = ClientConfig(self.app_config)

        if self.auto_load_config:
            try:
                await self._server_config.load()
                await self._client_config.load()
            except Exception as e:
                self._logger.warning("Could not load configurations", error=str(e))

        self._pre_configure()

        try:
            if IS_WINDOWS:
                await self._start_tcp_server()
            else:
                await self._start_unix_server()

            self._running = True
            # Signal handlers need a running loop + the daemon's bg_tasks.
            self._setup_signal_handlers()
            self._logger.debug(
                "Daemon started successfully", endpoint=self._endpoint_url
            )
            self._logger.info(
                f"Platform: {'Windows (TCP Socket)' if IS_WINDOWS else 'Unix (Socket)'}"
            )

            # Publish the actually-bound endpoint so GUI/tooling can
            # find us after a port fallback. Non-fatal on failure.
            if self._endpoint_url:
                try:
                    json_path, txt_path = write_endpoint(
                        ApplicationConfig.get_state_path(),
                        self._endpoint_url,
                        version=self.app_config.version,
                    )
                    self._logger.debug(
                        "Endpoint published", json_path=json_path, txt_path=txt_path
                    )
                except Exception as e:
                    self._logger.warning(
                        "Could not write endpoint file",
                        error=str(e),
                        fallback="falling back to legacy discovery",
                    )

            if sys.platform == "darwin":
                self._permission_watchdog_task = self._bg_tasks.spawn(
                    self._permission_watchdog(), name="permission_watchdog"
                )

            if service is not None and service in ("server", "client"):
                self._logger.info("Auto-starting service", service=service)
                if service == "server":
                    await self._handle_service_choice({"service": "server"})
                    self._bg_tasks.spawn(
                        self._handle_start_server({}), name="auto_start_server"
                    )
                elif service == "client":
                    await self._handle_service_choice({"service": "client"})
                    self._bg_tasks.spawn(
                        self._handle_start_client({}), name="auto_start_client"
                    )

            return True

        except (DaemonAlreadyRunningException, DaemonPortOccupiedException):
            raise

        except Exception as e:
            self._logger.error("Failed to start daemon", error=str(e))
            import traceback

            traceback_str = traceback.format_exc()
            self._logger.debug("Traceback", traceback=traceback_str)
            return False

    async def _start_unix_server(self):
        """Start Unix socket server, removing any stale socket file."""
        if os.path.exists(self.socket_path):
            self._logger.debug(
                f"Socket file {self.socket_path} already exists, checking if daemon is running..."
            )

            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_unix_connection(self.socket_path), timeout=2.0
                )
                # Connectable: a live daemon owns it.
                writer.close()
                await writer.wait_closed()

                error_msg = f"Daemon is already running on socket {self.socket_path}"
                self._logger.error(error_msg)
                raise DaemonAlreadyRunningException(error_msg)

            except (
                ConnectionRefusedError,
                FileNotFoundError,
                asyncio.TimeoutError,
            ) as e:
                self._logger.warning(
                    "Socket exists but daemon not running. Removing stale socket file...",
                    error=str(e),
                    socket_path=self.socket_path,
                )
                try:
                    os.unlink(self.socket_path)
                    self._logger.info("Removed stale socket file")
                except Exception as remove_error:
                    self._logger.error(
                        "Failed to remove stale socket", error=str(remove_error)
                    )
                    raise
            except OSError as e:
                if e.errno == errno.ENOTSOCK:
                    self._logger.warning(
                        f"File {self.socket_path} is not a socket. Removing it..."
                    )
                    try:
                        os.unlink(self.socket_path)
                        self._logger.info(
                            "Removed non-socket file", path=self.socket_path
                        )
                    except Exception as remove_error:
                        self._logger.error(
                            "Failed to remove non-socket file", error=str(remove_error)
                        )
                        raise

        # Tighten umask around bind so the socket inode is created with
        # mode 0o600 atomically.
        prev_umask = os.umask(0o077)
        try:
            self._socket_server = await asyncio.start_unix_server(
                self._handle_client_connection, path=self.socket_path
            )
        finally:
            os.umask(prev_umask)

        # Defensive: re-chmod in case a future asyncio override the umask.
        os.chmod(self.socket_path, 0o600)
        self._logger.debug("Unix socket server created", path=self.socket_path)
        self._endpoint_url = format_unix_endpoint(self.socket_path)

    async def _start_tcp_server(self):
        """Start TCP socket server on localhost (Windows).

        Probes the preferred port for a live daemon first; on failure
        walks forward through ``TCP_FALLBACK_PORT_RANGE`` adjacent ports.
        The actually-bound port is published via the endpoint file.
        """
        if ":" in self.socket_path:
            host, port_str = self.socket_path.split(":", 1)
            preferred_port = int(port_str)
        else:
            host = "127.0.0.1"
            preferred_port = ApplicationConfig.DEFAULT_DAEMON_PORT

        self._logger.debug(
            f"Checking if daemon is already running on {host}:{preferred_port}..."
        )

        # Only the preferred port carries "already running" semantics;
        # fallback ports may host unrelated services.
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, preferred_port), timeout=2.0
            )
            writer.close()
            await writer.wait_closed()

            error_msg = f"Daemon is already running on {host}:{preferred_port}"
            self._logger.error(error_msg)
            raise DaemonAlreadyRunningException(error_msg)

        except (ConnectionRefusedError, OSError) as e:
            self._logger.debug(
                "Port check failed as expected", error_type=type(e).__name__
            )
        except asyncio.TimeoutError:
            self._logger.warning(
                "Connection attempt timed out, proceeding with server creation"
            )

        last_err: Optional[OSError] = None
        for offset in range(self.TCP_FALLBACK_PORT_RANGE + 1):
            port = preferred_port + offset
            try:
                self._socket_server = await asyncio.start_server(
                    self._handle_client_connection, host=host, port=port
                )
                if offset > 0:
                    self._logger.warning(
                        f"Preferred port {preferred_port} busy; "
                        f"bound fallback port {port} instead"
                    )
                self._logger.info("TCP server started", host=host, port=port)
                # Keep socket_path in sync with the actually-bound port.
                self.socket_path = f"{host}:{port}"
                self._endpoint_url = format_tcp_endpoint(host, port)
                return
            except OSError as e:
                # errno 48 on macOS, 98 on Linux, 10048 on Windows
                if (
                    e.errno in (48, 98, 10048)
                    or "address already in use" in str(e).lower()
                ):
                    last_err = e
                    self._logger.info("Port busy, trying next fallback", port=port)
                    continue
                self._logger.error("Failed to start TCP server", error=str(e))
                raise

        # Exhausted all candidates.
        error_msg = (
            f"Could not bind any port in range "
            f"{preferred_port}..{preferred_port + self.TCP_FALLBACK_PORT_RANGE}"
        )
        self._logger.error(error_msg)
        raise DaemonPortOccupiedException(error_msg) from last_err

    async def stop(self):
        """Stop the daemon and cleanup resources."""
        if not self._running:
            self._logger.warning("Daemon not running")
            return

        self._logger.info("Stopping daemon...")

        self._running = False

        # Unregister signal handlers so a future ``start()`` in the same
        # process (tests, hot-restart) installs fresh ones cleanly.
        try:
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGTERM, signal.SIGINT):
                try:
                    loop.remove_signal_handler(sig)
                except (NotImplementedError, ValueError):
                    pass
        except RuntimeError:
            pass

        if self._permission_watchdog_task and not self._permission_watchdog_task.done():
            self._permission_watchdog_task.cancel()
            self._permission_watchdog_task = None

        async with self._client_connection_lock:
            if self._connected_client_writer is not None:
                try:
                    shutdown_msg = InfoEvent(
                        info="Daemon is shutting down", daemon_shutdown=True
                    )
                    self._connected_client_writer.write(
                        self.prepare_msg_bytes(shutdown_msg)
                    )
                    await self._connected_client_writer.drain()
                except Exception:
                    pass
                finally:
                    try:
                        self._connected_client_writer.close()
                        await self._connected_client_writer.wait_closed()
                    except Exception:
                        pass
                    self._connected_client_reader = None
                    self._connected_client_writer = None

        if self._server:
            await self._server.stop()
            self._server = None

        if self._client:
            await self._client.stop()
            self._client = None

        if self._socket_server:
            try:
                self._socket_server.close()
                await self._socket_server.wait_closed()
            except Exception as e:
                self._logger.error("Error closing socket server", error=str(e))

        if not IS_WINDOWS and os.path.exists(self.socket_path):
            os.unlink(self.socket_path)

        try:
            remove_endpoint(ApplicationConfig.get_state_path())
        except Exception as e:
            self._logger.debug("Failed to clean endpoint file", error=str(e))

        try:
            await asyncio.wait_for(self._bg_tasks.drain(), timeout=2.0)
        except asyncio.TimeoutError:
            await self._bg_tasks.drain(cancel=True)

        self._shutdown_event.set()
        self._logger.info("Daemon stopped")

        # Watchdog: after DELAYED_EXIT_TIMEOUT log diagnostics if the
        # loop still has tasks. ``os._exit`` only fires via
        # FORCE_EXIT_ENV_VAR — masking a stuck shutdown as success lies
        # about it and loses state on the way out.
        threading.Thread(target=self.delayed_exit, daemon=True).start()

    def delayed_exit(self):
        time.sleep(self.DELAYED_EXIT_TIMEOUT)
        # Clean shutdown + idle loop = nothing to escalate.
        try:
            loop = asyncio.get_event_loop()
            pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
        except RuntimeError:
            pending = []
        bg_count = len(self._bg_tasks)
        if self._shutdown_event.is_set() and not pending and bg_count == 0:
            return

        self._logger.error(
            "Daemon still has work in flight after shutdown timeout",
            timeout=self.DELAYED_EXIT_TIMEOUT,
            pending_tasks=[t.get_name() for t in pending][:20],
            pending_task_count=len(pending),
            bg_task_count=bg_count,
            shutdown_event_set=self._shutdown_event.is_set(),
        )
        if os.environ.get(self.FORCE_EXIT_ENV_VAR) == "1":
            self._logger.warning(
                f"{self.FORCE_EXIT_ENV_VAR}=1 set, calling os._exit(1)"
            )
            os._exit(1)

    async def wait_for_shutdown(self):
        await self._shutdown_event.wait()

    async def _handle_client_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ):
        """Handle the (single allowed) command-socket connection."""
        if IS_WINDOWS:
            addr = writer.get_extra_info("peername")
        else:
            addr = writer.get_extra_info("peername") or "local"

        self._logger.info("Instance connection attempt", address=addr)

        async with self._client_connection_lock:
            if self._connected_client_writer is not None:
                self._logger.warning(
                    "Rejecting connection: another instance already connected",
                    address=addr,
                )
                try:
                    error_response = ErrorEvent(
                        error="Another instance is already connected. Only one connection is allowed at a time."
                    )
                    writer.write(self.prepare_msg_bytes(error_response))
                    await writer.drain()
                except ConnectionResetError:
                    self._logger.warning(
                        "Connection reset while sending rejection message", address=addr
                    )
                except Exception as e:
                    self._logger.error(
                        f"Error sending rejection message ({e})", address=addr
                    )
                finally:
                    if writer:
                        try:
                            writer.close()
                            await writer.wait_closed()
                        except Exception:
                            pass
                return

            self._connected_client_writer = writer
            self._connected_client_reader = reader
            self._logger.debug("Instance connected", address=addr)

        try:
            welcome = InfoEvent(
                info="Connected to daemon", version=ApplicationConfig.version
            )
            await self._send_to_client(welcome)

            buff = bytearray()
            # ``scan_from`` marks how far into ``buff`` we've already scanned
            # for a frame trailer without success. Without it, a frame that
            # arrives across many small reads forces a full re-scan of the
            # buffer every cycle (O(N²)). On a successful parse it resets
            # to 0 because ``del buff[:bytes_read]`` shifts everything.
            scan_from = 0
            while self._running and not reader.at_eof():
                try:
                    data = await asyncio.wait_for(
                        reader.read(self.BUFFER_SIZE), timeout=1.0
                    )

                    if not data:
                        await asyncio.sleep(0.1)
                        continue

                    buff.extend(data)

                    commands_data, bytes_read = self.parse_msg_bytes(buff, scan_from)

                    if bytes_read > 0:
                        # In-place clear keeps the bytearray's allocation.
                        del buff[:bytes_read]
                        scan_from = 0
                    else:
                        # No new frame parsed: next iteration only needs to
                        # search the bytes we haven't looked at yet. Leave
                        # 4 bytes of slack so a trailer split exactly on
                        # the read boundary is still picked up.
                        scan_from = max(0, len(buff) - 4)

                    for command_data in commands_data:
                        try:
                            command = command_data.get("command")
                            if not isinstance(command, str):
                                raise ValueError("Missing or invalid 'command' field")
                            params = command_data.get("params", {})
                        except msgspec.DecodeError as e:
                            response = ErrorEvent(error=f"Invalid JSON ({e})")
                            await self._send_to_client(response)
                            continue

                        # Commands report via notifications; no inline response.
                        self._bg_tasks.spawn(
                            self._execute_command_throttled(command, params)
                        )
                        await asyncio.sleep(0)

                except asyncio.TimeoutError:
                    await asyncio.sleep(0)
                    continue
                except (
                    BrokenPipeError,
                    ConnectionResetError,
                    ConnectionAbortedError,
                ) as e:
                    self._logger.error("Instance disconnected", error=str(e))
                    break
                except Exception as e:
                    self._logger.error("Unhandled error", error=str(e))
                    response = ErrorEvent(error=f"Internal error ({e})")
                    try:
                        await self._send_to_client(response)
                    except Exception:
                        break
                    await asyncio.sleep(0.5)

        except Exception as e:
            self._logger.error("Unhandled error", error=str(e))

        finally:
            async with self._client_connection_lock:
                if self._connected_client_writer is writer:
                    self._connected_client_reader = None
                    self._connected_client_writer = None

            self._logger.info("Instance disconnected", address=addr)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    @staticmethod
    def prepare_msg_bytes(data: NotificationEvent | dict) -> bytes:
        """Encode an event/dict for the framed wire protocol."""
        if isinstance(data, dict):
            message_bytes = Daemon._encoder.encode(data)
        else:
            message_bytes = Daemon._encoder.encode(data.to_dict())
        length_prefix = len(message_bytes).to_bytes(4, byteorder="big")
        return message_bytes + length_prefix + b"\n"

    @staticmethod
    def parse_msg_bytes(
        data: "bytes | bytearray", scan_from: int = 0
    ) -> tuple[list[dict], int]:
        """Parse a buffer of length-prefixed, newline-delimited messages.

        ``scan_from`` is a hint - bytes before it have already been
        searched for a trailer and contain no newline. Used to avoid
        re-scanning the whole buffer when a frame straddles several
        reads. Returns ``(parsed_messages, bytes_consumed)``.
        """
        offset = 0
        d_len = len(data)
        try:
            if d_len <= 5:
                return [], 0
            lines = []
            search_from = max(scan_from, 4)
            while offset < d_len - 5:
                if offset + 4 > d_len:
                    raise ValueError("Incomplete length prefix")
                idx = data.find(b"\n", max(search_from, offset + 4))
                if idx == -1:
                    # Partial trailer — wait for more bytes.
                    break
                length_bytes = data[idx - 4 : idx]
                msg_length = int.from_bytes(length_bytes, byteorder="big")
                msg_data = data[offset : offset + msg_length]
                lines.append(Daemon._decoder.decode(msg_data))
                offset += msg_length + 5
                # After the first hit ``search_from`` is no longer ahead
                # of ``offset``; reset so the next find starts there.
                search_from = offset
            return lines, offset
        except msgspec.DecodeError as e:
            raise ValueError(f"Invalid msgspec data ({e})")
        except ValueError:
            raise

    async def _send_to_client(self, event: NotificationEvent) -> bool:
        async with self._client_connection_lock:
            if self._connected_client_writer is None:
                self._logger.warning("No client connected, cannot send notification")
                return False

            try:
                self._connected_client_writer.write(self.prepare_msg_bytes(event))
                await self._connected_client_writer.drain()
                return True
            except Exception as e:
                self._logger.error("Unhandled error", error=str(e))
                # Broken connection — clear so the next call doesn't retry.
                self._connected_client_reader = None
                self._connected_client_writer = None
                return False

    async def _send_notification(self, event: NotificationEvent) -> None:
        if not self.is_client_connected():
            return

        await self._send_to_client(event)

    async def _service_notification_callback(self, event: NotificationEvent) -> None:
        """Forward a service event through the notification manager."""
        self._bg_tasks.spawn(self._notification_manager.notify_event(event))

    def is_client_connected(self) -> bool:
        return self._connected_client_writer is not None

    def _get_active_service(
        self, service_type: str = "auto"
    ) -> tuple[Optional[Any], str, Optional[str]]:
        """Returns ``(service, name, error)`` for ``"auto"``/``"server"``/``"client"``."""
        if service_type == "auto":
            if self._server:
                return self._server, "server", None
            elif self._client:
                return self._client, "client", None
            else:
                return None, "", "No service is running"
        elif service_type == "server":
            if not self._server:
                return None, "", "Server is not running"
            return self._server, "server", None
        elif service_type == "client":
            if not self._client:
                return None, "", "Client is not running"
            return self._client, "client", None
        else:
            return None, "", f"Invalid service type: {service_type}"

    def _get_service_and_config(
        self, service_type: str = "auto"
    ) -> tuple[Optional[Any], Optional[Any], str, Optional[str]]:
        """Like :meth:`_get_active_service`, also returning the config."""
        if service_type == "auto":
            if self._server:
                return self._server, self._server_config, "server", None
            elif self._client:
                return self._client, self._client_config, "client", None
            else:
                return None, None, "", "No service initialized"
        elif service_type == "server":
            if not self._server:
                return None, None, "", "Server not initialized"
            return self._server, self._server_config, "server", None
        elif service_type == "client":
            if not self._client:
                return None, None, "", "Client not initialized"
            return self._client, self._client_config, "client", None
        else:
            return None, None, "", f"Invalid service type: {service_type}"

    async def _execute_command_throttled(
        self, command: str, params: Dict[str, Any]
    ) -> None:
        """Semaphore-bounded variant: caps in-flight commands."""
        async with self._command_semaphore:
            await self._execute_command(command, params)

    async def _execute_command(self, command: str, params: Dict[str, Any]) -> None:
        handler = self._command_handlers.get(command)
        if not handler:
            await self._notification_manager.notify_error(
                f"Unknown command: {command}", data={"command": command}
            )
            return

        try:
            await handler(params)
        except Exception as e:
            self._logger.error(
                "Command execution failed", command=command, error=str(e)
            )
            await self._notification_manager.notify_error(
                f"Command execution failed: {str(e)}",
                data={"command": command, "error": str(e)},
            )

    @CommandHandler.register(DaemonCommand.SERVICE_CHOICE)
    async def _handle_service_choice(self, params: Dict[str, Any]) -> None:
        """Handle service choice between client and server."""
        choice = params.get("service")
        command = DaemonCommand.SERVICE_CHOICE.value

        if choice == "server":
            if not self._server_config:
                await self._notification_manager.notify_command_error(
                    command, "Server configuration not initialized"
                )
                return
            if not self._server:
                self._logger.set_level(self._server_config.log_level)
                self._server = Server(
                    app_config=self.app_config,
                    server_config=self._server_config,
                    auto_load_config=False,
                )
                self._server.set_notification_callback(
                    self._service_notification_callback
                )
            if self._client and self._client.is_running():
                await self._notification_manager.notify_command_error(
                    command, "Cannot start server while client is running"
                )
                return
            elif self._client:
                await self._client.stop()
                self._client = None

            await self._notification_manager.notify_command_success(command, choice)

        elif choice == "client":
            if not self._client_config:
                await self._notification_manager.notify_command_error(
                    command, "Client configuration not initialized"
                )
                return
            if not self._client:
                self._logger.set_level(self._client_config.log_level)
                self._client = Client(
                    app_config=self.app_config,
                    client_config=self._client_config,
                    auto_load_config=False,
                )
                self._client.set_notification_callback(
                    self._service_notification_callback
                )
            if self._server and self._server.is_running():
                await self._notification_manager.notify_command_error(
                    command, "Cannot start client while server is running"
                )
                return
            elif self._server:
                await self._server.stop()
                self._server = None

            await self._notification_manager.notify_command_success(command, choice)
        else:
            await self._notification_manager.notify_command_error(
                command, "Invalid service choice"
            )

    @CommandHandler.register(DaemonCommand.START_SERVER)
    async def _handle_start_server(self, params: Dict[str, Any]) -> None:
        """Start the server service."""
        command = DaemonCommand.START_SERVER.value

        if self._server and self._server.is_running():
            await self._notification_manager.notify_command_error(
                command, "Server already running"
            )
            return

        if self._client and self._client.is_running():
            await self._notification_manager.notify_command_error(
                command, "Cannot start server while client is running"
            )
            return

        try:
            if not self._server:
                await self._notification_manager.notify_command_error(
                    command, "Server not initialized"
                )
                return

            try:
                success = await self._server.start()
            except ServerStartError as start_err:
                # Known, user-actionable failure (e.g. port in use): forward
                # the specific message so the GUI shows something useful.
                self._logger.error(str(start_err))
                await self._notification_manager.notify_command_error(
                    command, str(start_err)
                )
                return

            if success:
                self._state["server"].start()
                response_data = {
                    "host": self._server.config.host,
                    "port": self._server.config.port,
                    "start_time": self._state["server"].get_timestamp(),
                    "enabled_streams": self._server.get_enabled_streams(),
                }

                await self._notification_manager.notify_command_success(
                    command, "Server started successfully", result_data=response_data
                )
            else:
                await self._notification_manager.notify_command_error(
                    command, "Failed to start server"
                )
        except Exception as e:
            self._logger.error("Unhandled error", error=str(e))
            await self._notification_manager.notify_command_error(command, f"{str(e)}")

    @CommandHandler.register(DaemonCommand.STOP_SERVER)
    async def _handle_stop_server(self, params: Dict[str, Any]) -> None:
        """Stop the server service."""
        command = DaemonCommand.STOP_SERVER.value

        if not self._server or not self._server.is_running():
            await self._notification_manager.notify_command_error(
                command, "Server not running"
            )
            return

        try:
            await self._server.stop()
            self._state["server"].stop()
            await self._notification_manager.notify_command_success(
                command, "Server stopped successfully"
            )
        except Exception as e:
            await self._notification_manager.notify_command_error(command, f"{str(e)}")

    @CommandHandler.register(DaemonCommand.START_CLIENT)
    async def _handle_start_client(self, params: Dict[str, Any]) -> None:
        """Start the client service."""
        command = DaemonCommand.START_CLIENT.value

        if self._client and self._client.is_running():
            await self._notification_manager.notify_command_error(
                command, "Client already running"
            )
            return

        if self._server and self._server.is_running():
            await self._notification_manager.notify_command_error(
                command, "Cannot start client while server is running"
            )
            return

        if not self._client:
            await self._notification_manager.notify_command_error(
                command, "Client not initialized"
            )
            return

        try:
            success = await self._client.start()
            if success:
                self._state["client"].start()
                response_data = {
                    **self._client.config.server_info.to_dict(),
                    "start_time": self._state["client"].get_timestamp(),
                    "enabled_streams": self._client.get_enabled_streams(),
                }

                await self._notification_manager.notify_command_success(
                    command, "Client started successfully", result_data=response_data
                )
            else:
                await self._notification_manager.notify_command_error(
                    command, "Failed to start client"
                )
        except Exception as e:
            self._logger.error("Unhandled error", error=str(e))
            await self._notification_manager.notify_command_error(command, f"{str(e)}")

    @CommandHandler.register(DaemonCommand.STOP_CLIENT)
    async def _handle_stop_client(self, params: Dict[str, Any]) -> None:
        """Stop the client service."""
        command = DaemonCommand.STOP_CLIENT.value

        if not self._client:
            await self._notification_manager.notify_command_error(
                command, "Client not running"
            )
            return

        try:
            res = await self._client.stop()
            if not res:
                await self._notification_manager.notify_command_error(
                    command, "Failed to stop client"
                )
                return

            await self._notification_manager.notify_command_success(
                command, "Client stopped successfully"
            )
        except Exception as e:
            await self._notification_manager.notify_command_error(command, f"{str(e)}")

    @CommandHandler.register(DaemonCommand.STATUS)
    async def _handle_status(self, params: Dict[str, Any]) -> None:
        """Get overall daemon status."""
        command = DaemonCommand.STATUS.value

        status = {
            "platform": "windows" if IS_WINDOWS else "unix",
            "socket_path": self.socket_path,
            "client_connected": self.is_client_connected(),
        }

        if self._server_config and self._server:
            # Server-local monitor list — the GUI needs it for the
            # layout editor. Skipped silently when Screen can't
            # enumerate displays.
            try:
                from utils.screen import Screen

                server_monitors = [m.to_dict() for m in Screen.get_monitors()]
            except Exception as e:
                self._logger.debug(
                    f"Could not enumerate server monitors for status: {e}"
                )
                server_monitors = []

            # Pending approvals: GUI may launch AFTER the daemon, so a
            # client already waiting in pending-approval state would
            # otherwise be invisible until the next handshake attempt.
            try:
                pending_approvals = self._server.get_pending_approvals()
            except Exception as e:
                self._logger.debug(
                    f"Could not enumerate pending approvals for status: {e}"
                )
                pending_approvals = []

            status["server_info"] = {
                **self._server_config.to_dict(),
                "running": self._server.is_running(),
                "start_time": self._state["server"].get_timestamp(),
                "monitors": server_monitors,
                "pending_approvals": pending_approvals,
            }  # ty:ignore[invalid-assignment]

        if self._client_config and self._client:
            status["client_info"] = {
                **self._client_config.to_dict(),
                "running": self._client.is_running(),
                "connected": self._client.is_connected(),
                "start_time": self._state["client"].get_timestamp(),
                "otp_needed": await self._client.otp_needed(),
                "service_choice_needed": await self._client.server_choice_needed(),
            }  # ty:ignore[invalid-assignment]

            if await self._client.server_choice_needed():
                status["client_info"]["available_servers"] = [  # ty:ignore[invalid-assignment]
                    s.as_dict() for s in self._client.get_found_servers()
                ]

        await self._notification_manager.notify_command_success(
            command, "Status retrieved", result_data=status
        )

    @CommandHandler.register(DaemonCommand.GET_SERVER_CONFIG)
    async def _handle_get_server_config(self, params: Dict[str, Any]) -> None:
        """Get server configuration."""
        command = DaemonCommand.GET_SERVER_CONFIG.value

        if not self._server_config:
            await self._notification_manager.notify_command_error(
                command, "Server configuration not initialized"
            )
            return

        config_dict = {
            "uid": self._server_config.uid,
            "host": self._server_config.host,
            "port": self._server_config.port,
            "heartbeat_interval": self._server_config.heartbeat_interval,
            "ssl_enabled": self._server_config.ssl_enabled,
            "log_level": self._server_config.log_level,
            "streams_enabled": self._server_config.streams_enabled,
        }
        await self._notification_manager.notify_command_success(
            command, "Server configuration retrieved", result_data=config_dict
        )

    @CommandHandler.register(DaemonCommand.SET_SERVER_CONFIG)
    async def _handle_set_server_config(self, params: Dict[str, Any]) -> None:
        """Set server configuration."""
        command = DaemonCommand.SET_SERVER_CONFIG.value

        if not self._server_config:
            await self._notification_manager.notify_command_error(
                command, "Server configuration not initialized"
            )
            return

        try:
            if "uid" in params:
                self._server_config.uid = params.get("uid")

            if "host" in params or "port" in params:
                host = params.get("host", self._server_config.host)
                port = params.get("port", self._server_config.port)
                self._server_config.set_connection_params(host=host, port=port)

            if "heartbeat_interval" in params:
                self._server_config.heartbeat_interval = params["heartbeat_interval"]
            if "ssl_enabled" in params:
                if params["ssl_enabled"]:
                    self._server_config.enable_ssl()
                else:
                    self._server_config.disable_ssl()
            if "log_level" in params:
                self._server_config.set_logging(level=params["log_level"])
            if "streams_enabled" in params:
                self._server_config.streams_enabled = params["streams_enabled"]

            if self._server:
                await self._server.save_config()

            await self._notification_manager.notify_command_success(
                command, "Server configuration updated"
            )
        except Exception as e:
            await self._notification_manager.notify_command_error(command, f"{str(e)}")

    @CommandHandler.register(DaemonCommand.GET_CLIENT_CONFIG)
    async def _handle_get_client_config(self, params: Dict[str, Any]) -> None:
        """Get client configuration."""
        command = DaemonCommand.GET_CLIENT_CONFIG.value

        if not self._client_config:
            await self._notification_manager.notify_command_error(
                command, "Client configuration not initialized"
            )
            return

        config_dict = {
            "server_host": self._client_config.get_server_host(),
            "server_port": self._client_config.get_server_port(),
            "heartbeat_interval": self._client_config.get_heartbeat_interval(),
            "auto_reconnect": self._client_config.do_auto_reconnect(),
            "ssl_enabled": self._client_config.ssl_enabled,
            "log_level": self._client_config.log_level,
            "streams_enabled": self._client_config.streams_enabled,
            "hostname": self._client_config.get_hostname(),
            "uid": self._client_config.uid,
        }
        await self._notification_manager.notify_command_success(
            command, "Client configuration retrieved", result_data=config_dict
        )

    @CommandHandler.register(DaemonCommand.SET_CLIENT_CONFIG)
    async def _handle_set_client_config(self, params: Dict[str, Any]) -> None:
        """Set client configuration."""
        command = DaemonCommand.SET_CLIENT_CONFIG.value

        if not self._client_config:
            await self._notification_manager.notify_command_error(
                command, "Client configuration not initialized"
            )
            return

        try:
            if (
                "server_host" in params
                or "server_port" in params
                or "server_hostname" in params
                or "auto_reconnect" in params
            ):
                host = params.get("server_host", self._client_config.get_server_host())
                hostname = params.get(
                    "server_hostname", self._client_config.get_server_hostname()
                )
                port = params.get("server_port", self._client_config.get_server_port())
                uid = params.get("server_uid", self._client_config.get_server_uid())
                auto_reconnect = params.get(
                    "auto_reconnect", self._client_config.do_auto_reconnect()
                )

                if host == "" and hostname == "":
                    uid = ""

                self._client_config.set_server_connection(
                    uid=uid,
                    host=host,
                    hostname=hostname,
                    port=port,
                    auto_reconnect=auto_reconnect,
                )

            if "heartbeat_interval" in params:
                self._client_config.heartbeat_interval = int(  # ty:ignore[invalid-assignment]
                    params.get(
                        "heartbeat_interval",
                        self._client_config.get_heartbeat_interval(),
                    )
                )
            if "ssl_enabled" in params:
                if params["ssl_enabled"]:
                    self._client_config.enable_ssl()
                else:
                    self._client_config.disable_ssl()
            if "log_level" in params:
                self._client_config.set_logging(level=params["log_level"])
            if "streams_enabled" in params:
                self._client_config.streams_enabled = params["streams_enabled"]
            if "client_hostname" in params:
                self._client_config.set_hostname(params["client_hostname"])
            if "uid" in params:
                self._client_config.uid = params.get("uid")

            if self._client:
                await self._client.save_config()

            await self._notification_manager.notify_command_success(
                command, "Client configuration updated"
            )
        except Exception as e:
            await self._notification_manager.notify_command_error(command, f"{str(e)}")

    @CommandHandler.register(DaemonCommand.SAVE_CONFIG)
    async def _handle_save_config(self, params: Dict[str, Any]) -> None:
        """Save configurations to disk."""
        command = DaemonCommand.SAVE_CONFIG.value

        try:
            config_type = params.get("type", "both")

            if config_type in ("server", "both"):
                if not self._server_config:
                    raise Exception("Server configuration not initialized")
                await self._server_config.save()

            if config_type in ("client", "both"):
                if not self._client_config:
                    raise Exception("Client configuration not initialized")
                await self._client_config.save()

            await self._notification_manager.notify_command_success(
                command, f"Configuration saved ({config_type})"
            )
        except Exception as e:
            await self._notification_manager.notify_command_error(command, f"{str(e)}")

    @CommandHandler.register(DaemonCommand.RELOAD_CONFIG)
    async def _handle_reload_config(self, params: Dict[str, Any]) -> None:
        """Reload configurations from disk."""
        command = DaemonCommand.RELOAD_CONFIG.value

        try:
            config_type = params.get("type", "both")

            if config_type in ("server", "both"):
                if not self._server_config:
                    raise Exception("Server configuration not initialized")
                await self._server_config.load()

            if config_type in ("client", "both"):
                if not self._client_config:
                    raise Exception("Client configuration not initialized")
                await self._client_config.load()

            await self._notification_manager.notify_command_success(
                command, f"Configuration reloaded ({config_type})"
            )
        except Exception as e:
            await self._notification_manager.notify_command_error(command, f"{str(e)}")

    @CommandHandler.register(DaemonCommand.GET_AUTOSTART)
    async def _handle_get_autostart(self, params: Dict[str, Any]) -> None:
        """Return whether the GUI is registered to start at login.

        The result payload also includes the executable currently registered
        so the GUI can detect a stale pointer after an install path change.
        """
        command = DaemonCommand.GET_AUTOSTART.value
        try:
            from utils.autostart import AutostartManager

            status = AutostartManager().is_enabled()
            await self._notification_manager.notify_command_success(
                command,
                "Autostart status retrieved",
                result_data={
                    "enabled": status.enabled,
                    "exec_path": status.exec_path,
                    # ``mode`` is one of off/server/client/plain and is what the
                    # GUI uses to reflect the selected launch mode in the tray.
                    "mode": status.mode,
                },
            )
        except Exception as e:
            await self._notification_manager.notify_command_error(command, f"{str(e)}")

    @CommandHandler.register(DaemonCommand.SET_AUTOSTART)
    async def _handle_set_autostart(self, params: Dict[str, Any]) -> None:
        """Enable or disable launch-at-login for the GUI, selecting the mode.

        Params:
          - ``mode`` (str, optional): one of ``off`` / ``server`` / ``client``
            / ``plain``. ``off`` removes the entry; ``server`` / ``client``
            make the app auto-start that service at login; ``plain`` just
            launches the app minimized. When present, ``mode`` drives the
            behaviour and derives the launch args (ignoring ``enabled`` /
            ``args``).
          - ``enabled`` (bool, legacy): used only when ``mode`` is absent.
          - ``exec_path`` (str, required unless disabling): absolute path to
            the Tauri GUI executable. The GUI knows its own path so we don't
            try to guess it here.
          - ``args`` (list[str], legacy): explicit launch args; only honoured
            when ``mode`` is absent. Defaults to ``["--start-minimized"]``.
        """
        command = DaemonCommand.SET_AUTOSTART.value
        try:
            from utils.autostart import MODE_OFF, AutostartManager, args_for_mode

            mode = params.get("mode")
            exec_path = params.get("exec_path")

            if mode is not None:
                # Mode-driven path (current GUI): translate the mode into the
                # concrete launch args.
                enabled = mode != MODE_OFF
                args = args_for_mode(mode) if enabled else None
            else:
                # Legacy path: explicit enabled/args.
                enabled = bool(params.get("enabled"))
                args = params.get("args")

            mgr = AutostartManager()
            if enabled:
                if not exec_path:
                    raise ValueError("'exec_path' is required when enabling autostart")
                mgr.enable(exec_path, args=args)
            else:
                mgr.disable()

            status = mgr.is_enabled()
            await self._notification_manager.notify_command_success(
                command,
                f"Autostart set to {status.mode}",
                result_data={
                    "enabled": status.enabled,
                    "exec_path": status.exec_path,
                    "mode": status.mode,
                },
            )
        except Exception as e:
            await self._notification_manager.notify_command_error(command, f"{str(e)}")

    @CommandHandler.register(DaemonCommand.ENABLE_STREAM)
    async def _handle_enable_stream(self, params: Dict[str, Any]) -> None:
        """Enable a stream on the running service."""
        command = DaemonCommand.ENABLE_STREAM.value
        stream_type = params.get("stream_type")
        service_type = params.get("service", "auto")

        if stream_type is None:
            await self._notification_manager.notify_command_error(
                command, "Missing 'stream_type' parameter"
            )
            return

        try:
            if isinstance(stream_type, str):
                stream_type = int(stream_type)

            service, service_name, error = self._get_active_service(service_type)
            if error:
                await self._notification_manager.notify_command_error(command, error)
                return

            if not service:
                await self._notification_manager.notify_command_error(
                    command, "No active service to enable stream on"
                )
                return

            res: bool = await service.enable_stream_runtime(stream_type)
            if not res:
                raise Exception("Stream could not be enabled")

            result_data = {
                "service": service_name,
                "stream_type": stream_type,
                "active_streams": service.get_active_streams(),
            }
            await self._notification_manager.notify_command_success(
                command,
                f"Stream {stream_type} enabled on {service_name}",
                result_data=result_data,
            )
        except Exception as e:
            await self._notification_manager.notify_command_error(command, f"{str(e)}")

    @CommandHandler.register(DaemonCommand.DISABLE_STREAM)
    async def _handle_disable_stream(self, params: Dict[str, Any]) -> None:
        """Disable a stream on the running service."""
        command = DaemonCommand.DISABLE_STREAM.value
        stream_type = params.get("stream_type")
        service_type = params.get("service", "auto")

        if stream_type is None:
            await self._notification_manager.notify_command_error(
                command, "Missing 'stream_type' parameter"
            )
            return

        try:
            if isinstance(stream_type, str):
                stream_type = int(stream_type)

            service, service_name, error = self._get_active_service(service_type)
            if error:
                await self._notification_manager.notify_command_error(command, error)
                return

            if not service:
                await self._notification_manager.notify_command_error(
                    command, "No active service to disable stream on"
                )
                return

            await service.disable_stream_runtime(stream_type)

            result_data = {
                "service": service_name,
                "stream_type": stream_type,
                "active_streams": service.get_active_streams(),
            }
            await self._notification_manager.notify_command_success(
                command,
                f"Stream {stream_type} disabled on {service_name}",
                result_data=result_data,
            )
        except Exception as e:
            await self._notification_manager.notify_command_error(command, f"{str(e)}")

    @CommandHandler.register(DaemonCommand.GET_STREAMS)
    async def _handle_get_streams(self, params: Dict[str, Any]) -> None:
        """Get stream information."""
        command = DaemonCommand.GET_STREAMS.value
        service_type = params.get("service", "auto")

        service, service_name, error = self._get_active_service(service_type)
        if error:
            await self._notification_manager.notify_command_error(command, error)
            return

        result_data = {
            "service": service_name,
            "enabled_streams": service.get_enabled_streams() if service else [],
            "active_streams": service.get_active_streams() if service else [],
        }
        await self._notification_manager.notify_command_success(
            command, f"Streams info for {service_name}", result_data=result_data
        )

    @CommandHandler.register(DaemonCommand.ADD_CLIENT)
    async def _handle_add_client(self, params: Dict[str, Any]) -> None:
        """Add a client to the server allowlist."""
        command = DaemonCommand.ADD_CLIENT.value

        if not self._server:
            await self._notification_manager.notify_command_error(
                command, "Server is not running"
            )
            return

        try:
            hostname = params.get("hostname")
            # Back-compat: legacy ``ip_address`` (str) vs ``ip_addresses`` (list).
            ip_addresses = params.get("ip_addresses", params.get("ip_address"))
            screen_position = params.get("screen_position")

            if not hostname and not ip_addresses:
                await self._notification_manager.notify_command_error(
                    command, "Must provide either hostname or ip_addresses"
                )
                return

            # No screen_position = unplaced; admin runs the Layout Editor
            # to position monitors. Legacy callers still hit the synthesis
            # fallback in :meth:`ClientObj.get_effective_placements`.
            await self._server.add_client(
                hostname=hostname,
                ip_addresses=ip_addresses,
                screen_position=screen_position,
            )

            placement_hint = (
                f"at position {screen_position}"
                if screen_position
                else "(unplaced - open the Layout Editor to place its monitors)"
            )
            await self._notification_manager.notify_command_success(
                command,
                f"Client added {placement_hint}",
                result_data={
                    "hostname": hostname,
                    "ip_addresses": ip_addresses,
                    "screen_position": screen_position,
                },
            )
        except Exception as e:
            await self._notification_manager.notify_command_error(command, f"{str(e)}")

    @CommandHandler.register(DaemonCommand.REMOVE_CLIENT)
    async def _handle_remove_client(self, params: Dict[str, Any]) -> None:
        """Remove a client from the server allowlist."""
        command = DaemonCommand.REMOVE_CLIENT.value

        if not self._server:
            await self._notification_manager.notify_command_error(
                command, "Server is not running"
            )
            return

        try:
            hostname = params.get("hostname")
            ip_address = params.get("ip_address")

            if not hostname and not ip_address:
                await self._notification_manager.notify_command_error(
                    command, "Must provide either hostname or ip_address"
                )
                return

            await self._server.remove_client(hostname=hostname, ip_address=ip_address)

            await self._notification_manager.notify_command_success(
                command,
                "Client removed",
                result_data={"hostname": hostname, "ip_address": ip_address},
            )
        except Exception as e:
            await self._notification_manager.notify_command_error(command, f"{str(e)}")

    @CommandHandler.register(DaemonCommand.APPROVE_CLIENT)
    async def _handle_approve_client(self, params: Dict[str, Any]) -> None:
        """Approve a pending client."""
        command = DaemonCommand.APPROVE_CLIENT.value

        if not self._server:
            await self._notification_manager.notify_command_error(
                command, "Server is not running"
            )
            return

        try:
            peer_ip = params.get("peer_ip") or params.get("ip_address")
            # Optional: omitted means unplaced; GUI opens the Layout Editor.
            screen_position = params.get("screen_position")
            if not peer_ip:
                await self._notification_manager.notify_command_error(
                    command, "Must provide peer_ip"
                )
                return

            ok = await self._server.approve_pending_client(
                peer_ip=peer_ip, screen_position=screen_position
            )
            if ok:
                await self._notification_manager.notify_command_success(
                    command,
                    f"Client {peer_ip} approved at {screen_position}",
                    result_data={
                        "peer_ip": peer_ip,
                        "screen_position": screen_position,
                    },
                )
            else:
                await self._notification_manager.notify_command_error(
                    command,
                    f"No pending approval for {peer_ip} (already resolved or timed out)",
                )
        except Exception as e:
            await self._notification_manager.notify_command_error(command, f"{str(e)}")

    @CommandHandler.register(DaemonCommand.DENY_CLIENT)
    async def _handle_deny_client(self, params: Dict[str, Any]) -> None:
        """Deny a pending client."""
        command = DaemonCommand.DENY_CLIENT.value

        if not self._server:
            await self._notification_manager.notify_command_error(
                command, "Server is not running"
            )
            return

        try:
            peer_ip = params.get("peer_ip") or params.get("ip_address")
            if not peer_ip:
                await self._notification_manager.notify_command_error(
                    command, "Must provide peer_ip"
                )
                return

            ok = await self._server.deny_pending_client(peer_ip=peer_ip)
            if ok:
                await self._notification_manager.notify_command_success(
                    command,
                    f"Client {peer_ip} denied",
                    result_data={"peer_ip": peer_ip},
                )
            else:
                await self._notification_manager.notify_command_error(
                    command,
                    f"No pending approval for {peer_ip}",
                )
        except Exception as e:
            await self._notification_manager.notify_command_error(command, f"{str(e)}")

    @CommandHandler.register(DaemonCommand.LIST_PENDING_APPROVALS)
    async def _handle_list_pending_approvals(self, params: Dict[str, Any]) -> None:
        """List currently pending client-approval requests."""
        command = DaemonCommand.LIST_PENDING_APPROVALS.value

        if not self._server:
            await self._notification_manager.notify_command_error(
                command, "Server is not running"
            )
            return

        try:
            pending = self._server.get_pending_approvals()
            await self._notification_manager.notify_command_success(
                command,
                f"{len(pending)} pending approval(s)",
                result_data={"pending": pending, "count": len(pending)},
            )
        except Exception as e:
            await self._notification_manager.notify_command_error(command, f"{str(e)}")

    @CommandHandler.register(DaemonCommand.EDIT_CLIENT)
    async def _handle_edit_client(self, params: Dict[str, Any]) -> None:
        """Edit a client configuration."""
        command = DaemonCommand.EDIT_CLIENT.value

        if not self._server:
            await self._notification_manager.notify_command_error(
                command, "Server is not enabled"
            )
            return

        try:
            hostname = params.get("hostname")
            ip_address = params.get("ip_address")
            new_screen_position = params.get("new_screen_position")
            new_ip_addresses = params.get("new_ip_addresses")

            if not hostname and not ip_address:
                await self._notification_manager.notify_command_error(
                    command, "Must provide either hostname or ip_address"
                )
                return

            if not new_screen_position and new_ip_addresses is None:
                await self._notification_manager.notify_command_error(
                    command, "Must provide new_screen_position or new_ip_addresses"
                )
                return

            await self._server.edit_client(
                hostname=hostname,
                ip_address=ip_address,
                new_screen_position=new_screen_position,
                new_ip_addresses=new_ip_addresses,
            )

            await self._notification_manager.notify_command_success(
                command,
                "Client updated",
                result_data={
                    "hostname": hostname,
                    "ip_address": ip_address,
                    "new_screen_position": new_screen_position,
                    "new_ip_addresses": new_ip_addresses,
                },
            )
        except Exception as e:
            await self._notification_manager.notify_command_error(command, f"{str(e)}")

    @CommandHandler.register(DaemonCommand.SET_CLIENT_LAYOUT)
    async def _handle_set_client_layout(self, params: Dict[str, Any]) -> None:
        """Persist the workspace placements of one client.

        Params: ``client_uid`` / ``hostname`` / ``ip_address`` (lookup),
        ``placements`` (list of
        ``{client_monitor_id, workspace_x, workspace_y, width, height}``).
        """
        command = DaemonCommand.SET_CLIENT_LAYOUT.value

        if not self._server:
            await self._notification_manager.notify_command_error(
                command, "Server is not enabled"
            )
            return

        try:
            placements = params.get("placements", []) or []
            client_uid = params.get("client_uid")
            hostname = params.get("hostname")
            ip_address = params.get("ip_address")
            if not (client_uid or hostname or ip_address):
                await self._notification_manager.notify_command_error(
                    command,
                    "Must provide client_uid, hostname, or ip_address",
                )
                return

            updated = await self._server.set_client_layout(
                placements=placements,
                uid=client_uid,
                hostname=hostname,
                ip_address=ip_address,
            )

            await self._notification_manager.notify_command_success(
                command,
                f"Layout updated for {updated.get_net_id()}",
                result_data={
                    "client_uid": updated.uid,
                    "net_id": updated.get_net_id(),
                    "placements": list(updated.placements),
                },
            )
        except Exception as e:
            await self._notification_manager.notify_command_error(command, f"{str(e)}")

    @CommandHandler.register(DaemonCommand.LIST_CLIENTS)
    async def _handle_list_clients(self, params: Dict[str, Any]) -> None:
        """List registered clients."""
        command = DaemonCommand.LIST_CLIENTS.value

        if not self._server:
            await self._notification_manager.notify_command_error(
                command, "Server not initialized"
            )
            return

        try:
            clients = self._server.get_clients()
            clients_data = []

            for client in clients:
                clients_data.append(
                    {
                        "net_id": client.get_net_id(),
                        "hostname": client.host_name,
                        "ip_addresses": list(client.ip_addresses),
                        "screen_position": client.screen_position,
                        "is_connected": client.is_connected,
                    }
                )

            await self._notification_manager.notify_command_success(
                command,
                "Clients list retrieved",
                result_data={"count": len(clients_data), "clients": clients_data},
            )
        except Exception as e:
            await self._notification_manager.notify_command_error(command, f"{str(e)}")

    @CommandHandler.register(DaemonCommand.ENABLE_SSL)
    async def _handle_enable_ssl(self, params: Dict[str, Any]) -> None:
        """Enable SSL."""
        command = DaemonCommand.ENABLE_SSL.value
        service_type = params.get("service", "auto")

        service, config, service_name, error = self._get_service_and_config(
            service_type
        )
        if error:
            await self._notification_manager.notify_command_error(command, error)
            return

        if not config:
            await self._notification_manager.notify_command_error(
                command, f"{service_name.capitalize()} configuration not initialized"
            )
            return

        try:
            if hasattr(service, "enable_ssl"):
                result = service.enable_ssl()
                if result:
                    config.enable_ssl()
                    await self._notification_manager.notify_command_success(
                        command, f"SSL enabled on {service_name}"
                    )
                else:
                    await self._notification_manager.notify_command_error(
                        command, "Failed to enable SSL or to load certificates"
                    )
            else:
                config.enable_ssl()
                await self._notification_manager.notify_command_success(
                    command, f"SSL enabled in {service_name} config (restart required)"
                )
        except Exception as e:
            await self._notification_manager.notify_command_error(command, f"{str(e)}")

    @CommandHandler.register(DaemonCommand.DISABLE_SSL)
    async def _handle_disable_ssl(self, params: Dict[str, Any]) -> None:
        """Disable SSL."""
        command = DaemonCommand.DISABLE_SSL.value
        service_type = params.get("service", "auto")

        service, config, service_name, error = self._get_service_and_config(
            service_type
        )
        if error:
            await self._notification_manager.notify_command_error(command, error)
            return

        if not config:
            await self._notification_manager.notify_command_error(
                command, f"{service_name.capitalize()} configuration not initialized"
            )
            return

        try:
            if hasattr(service, "disable_ssl"):
                service.disable_ssl()
            config.disable_ssl()
            await self._notification_manager.notify_command_success(
                command, f"SSL disabled on {service_name}"
            )
        except Exception as e:
            await self._notification_manager.notify_command_error(command, f"{str(e)}")

    @CommandHandler.register(DaemonCommand.SHARE_CERTIFICATE)
    async def _handle_share_certificate(self, params: Dict[str, Any]) -> None:
        """Share certificate (server only)."""
        command = DaemonCommand.SHARE_CERTIFICATE.value

        if not self._server or not self._server.is_running():
            await self._notification_manager.notify_command_error(
                command, "Server is not running"
            )
            return

        try:
            host = params.get("host", self._server.config.host)
            timeout = params.get("timeout", 30)
            res, otp = await self._server.share_certificate(host=host, timeout=timeout)
            if res and otp:
                await self._notification_manager.send(
                    OtpGeneratedEvent(otp=otp, timeout=timeout)
                )
                await self._notification_manager.notify_command_success(
                    command,
                    "Certificate sharing started",
                    result_data={
                        "otp": otp,
                        "timeout": timeout,
                        "instructions": "Provide this OTP to clients",
                    },
                )
            else:
                await self._notification_manager.notify_command_error(
                    command, "Failed to share certificate"
                )
        except Exception as e:
            await self._notification_manager.notify_command_error(command, f"{str(e)}")

    @CommandHandler.register(DaemonCommand.RECEIVE_CERTIFICATE)
    async def _handle_receive_certificate(self, params: Dict[str, Any]) -> None:
        """Receive certificate (client only)."""
        command = DaemonCommand.RECEIVE_CERTIFICATE.value

        if not self._client:
            await self._notification_manager.notify_command_error(
                command, "Client not initialized"
            )
            return

        try:
            otp = params.get("otp")
            if not otp:
                await self._notification_manager.notify_command_error(
                    command, "Must provide 'otp' parameter"
                )
                return

            success = await self._client.set_otp(otp)

            if success:
                await self._notification_manager.notify_command_success(
                    command,
                    "Certificate received successfully",
                    result_data={
                        "certificate_path": self._client.get_certificate_path()
                    },
                )
            else:
                await self._notification_manager.notify_command_error(
                    command,
                    "Failed to receive certificate (invalid OTP or network error)",
                )
        except Exception as e:
            await self._notification_manager.notify_command_error(command, f"{str(e)}")

    @CommandHandler.register(DaemonCommand.CHECK_SERVER_CHOICE_NEEDED)
    async def _handle_check_server_choice_needed(self, params: Dict[str, Any]) -> None:
        """Check if server choice is needed (client only)."""
        command = DaemonCommand.CHECK_SERVER_CHOICE_NEEDED.value

        if not self._client:
            await self._notification_manager.notify_command_error(
                command, "Client not initialized"
            )
            return

        try:
            needed = await self._client.server_choice_needed()

            await self._notification_manager.notify_command_success(
                command,
                "Server choice status checked",
                result_data={
                    "server_choice_needed": needed,
                    "message": "Please choose a server from the found servers"
                    if needed
                    else "No server choice needed",
                },
            )
        except Exception as e:
            await self._notification_manager.notify_command_error(command, f"{str(e)}")

    @CommandHandler.register(DaemonCommand.GET_FOUND_SERVERS)
    async def _handle_get_found_servers(self, params: Dict[str, Any]) -> None:
        """Get list of found servers (client only)."""
        command = DaemonCommand.GET_FOUND_SERVERS.value

        if not self._client:
            await self._notification_manager.notify_command_error(
                command, "Client not initialized"
            )
            return

        try:
            servers = self._client.get_found_servers()

            servers_data = [s.as_dict() for s in servers]

            await self._notification_manager.notify_command_success(
                command,
                "Found servers retrieved",
                result_data={"servers": servers_data, "count": len(servers_data)},
            )
        except Exception as e:
            await self._notification_manager.notify_command_error(command, f"{str(e)}")

    @CommandHandler.register(DaemonCommand.CHOOSE_SERVER)
    async def _handle_choose_server(self, params: Dict[str, Any]) -> None:
        """Choose a server from found servers (client only)."""
        command = DaemonCommand.CHOOSE_SERVER.value

        if not self._client:
            await self._notification_manager.notify_command_error(
                command, "Client not initialized"
            )
            return

        try:
            uid = params.get("uid")
            if not uid:
                await self._notification_manager.notify_command_error(
                    command, "Must provide 'uid' parameter"
                )
                return

            self._client.choose_server(uid)

            await self._notification_manager.notify_command_success(
                command,
                f"Server {uid} selected",
                result_data={
                    "server_host": self._client.config.get_server_host(),
                    "server_port": self._client.config.get_server_port(),
                },
            )
        except Exception as e:
            await self._notification_manager.notify_command_error(command, f"{str(e)}")

    @CommandHandler.register(DaemonCommand.CHECK_OTP_NEEDED)
    async def _handle_check_otp_needed(self, params: Dict[str, Any]) -> None:
        """Check if OTP is needed (client only)."""
        command = DaemonCommand.CHECK_OTP_NEEDED.value

        if not self._client:
            await self._notification_manager.notify_command_error(
                command, "Client not initialized"
            )
            return

        try:
            needed = await self._client.otp_needed()

            await self._notification_manager.notify_command_success(
                command,
                "OTP status checked",
                result_data={
                    "otp_needed": needed,
                    "message": "Please provide OTP from server"
                    if needed
                    else "No OTP needed",
                },
            )
        except Exception as e:
            await self._notification_manager.notify_command_error(command, f"{str(e)}")

    @CommandHandler.register(DaemonCommand.REQUEST_PAIRING)
    async def _handle_request_pairing(self, params: Dict[str, Any]) -> None:
        """Ask the configured server to auto-generate an OTP.

        Fallback for the GUI's manual "Request OTP" button when the
        automatic request from the connection flow didn't land.
        """
        command = DaemonCommand.REQUEST_PAIRING.value

        if not self._client:
            await self._notification_manager.notify_command_error(
                command, "Client not initialized"
            )
            return

        try:
            host = params.get("host")
            port = params.get("port")
            timeout = params.get("timeout", 5)

            kwargs: Dict[str, Any] = {"timeout": timeout}
            if host:
                kwargs["server_host"] = host
            if port:
                kwargs["server_port"] = port

            success, ttl, err = await self._client.request_pairing(**kwargs)

            if success:
                await self._notification_manager.notify_command_success(
                    command,
                    "Pairing request sent",
                    result_data={"otp_validity_seconds": ttl},
                )
            else:
                await self._notification_manager.notify_command_error(
                    command, f"Pairing request failed: {err or 'unknown'}"
                )
        except Exception as e:
            await self._notification_manager.notify_command_error(command, f"{str(e)}")

    @CommandHandler.register(DaemonCommand.SET_OTP)
    async def _handle_set_otp(self, params: Dict[str, Any]) -> None:
        """Set OTP for certificate reception (client only)."""
        command = DaemonCommand.SET_OTP.value

        if not self._client:
            await self._notification_manager.notify_command_error(
                command, "Client not initialized"
            )
            return

        try:
            otp = params.get("otp")
            if not otp:
                await self._notification_manager.notify_command_error(
                    command, "Must provide a valid OTP"
                )
                return

            success = await self._client.set_otp(otp)

            if success:
                await self._notification_manager.notify_command_success(
                    command,
                    "OTP set successfully",
                    result_data={
                        "certificate_path": self._client.get_certificate_path()
                        if self._client.has_certificate()
                        else None,
                    },
                )
            else:
                await self._notification_manager.notify_command_error(
                    command, "Failed to set OTP (invalid format or already set)"
                )
        except Exception as e:
            await self._notification_manager.notify_command_error(command, f"{str(e)}")

    @CommandHandler.register(DaemonCommand.DISCOVER_SERVICES)
    async def _get_discovered_services(self, params: Dict[str, Any]) -> None:
        """Get available services on the network."""
        command = DaemonCommand.DISCOVER_SERVICES.value

        try:
            if self._client:
                services = self._client.get_found_servers()

                services_data = [
                    {
                        "name": s.name,
                        "address": s.address,
                        "port": s.port,
                        "hostname": s.hostname,
                        "uid": s.uid,
                    }
                    for s in services
                ]

                await self._notification_manager.notify_command_success(
                    command,
                    "Services discovered",
                    result_data={
                        "services": services_data,
                        "count": len(services_data),
                    },
                )
            else:
                await self._notification_manager.notify_command_error(
                    command, "Client not initialized; cannot perform service discovery"
                )
        except Exception as e:
            await self._notification_manager.notify_command_error(command, f"{str(e)}")

    @CommandHandler.register(DaemonCommand.SHUTDOWN)
    async def _handle_shutdown(self, params: Dict[str, Any]) -> None:
        """Shutdown the daemon."""
        command = DaemonCommand.SHUTDOWN.value

        await self._notification_manager.notify_command_success(
            command, "Daemon shutting down..."
        )

        # Defer the actual stop so the response notification flushes first.
        self._bg_tasks.spawn(self._delayed_shutdown())

    async def _delayed_shutdown(self):
        await asyncio.sleep(0.5)
        await self.stop()

    @CommandHandler.register(DaemonCommand.PING)
    async def _handle_ping(self, params: Dict[str, Any]) -> None:
        await self._notification_manager.notify_pong()

    def is_running(self) -> bool:
        return self._running

    def get_socket_path(self) -> str:
        return self.socket_path


async def send_daemon_command(
    command: str,
    params: Optional[Dict[str, Any]] = None,
    socket_path: Optional[str] = None,
    timeout: float = 30.0,
) -> Dict[str, Any]:
    """Send a command to the daemon and return the response dict."""
    socket_path = socket_path or Daemon.DEFAULT_SOCKET_PATH

    command_data = DaemonCommand.new(
        command=command, socket_path=socket_path, **(params or {})
    )

    if IS_WINDOWS or ":" in socket_path:
        return await _send_tcp_command(socket_path, command_data.to_dict(), timeout)
    else:
        return await _send_unix_command(socket_path, command_data.to_dict(), timeout)


async def _send_unix_command(
    socket_path: str, command_data: dict, timeout: float
) -> dict:
    if not os.path.exists(socket_path):
        raise ConnectionError(f"Daemon not running (socket not found: {socket_path})")

    reader, writer = await asyncio.wait_for(
        asyncio.open_unix_connection(socket_path), timeout=5.0
    )

    try:
        writer.write(Daemon.prepare_msg_bytes(command_data))
        await writer.drain()

        data = await asyncio.wait_for(reader.read(Daemon.BUFFER_SIZE), timeout=timeout)

        if not data:
            raise ConnectionError("No response from daemon")

        response = Daemon._decoder.decode(data)
        return response

    finally:
        writer.close()
        await writer.wait_closed()


async def _send_tcp_command(
    socket_path: str, command_data: dict, timeout: float
) -> dict:
    if ":" in socket_path:
        host, port_str = socket_path.split(":", 1)
        port = int(port_str)
    else:
        raise ValueError(
            f"Invalid TCP socket path format: {socket_path}. Expected host:port"
        )

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=5.0
        )
    except (ConnectionRefusedError, OSError) as e:
        raise ConnectionError(
            f"Daemon not running (cannot connect to {host}:{port}) ({e})"
        )

    try:
        writer.write(Daemon.prepare_msg_bytes(command_data))
        await writer.drain()

        data = await asyncio.wait_for(reader.read(Daemon.BUFFER_SIZE), timeout=timeout)

        if not data:
            raise ConnectionError("No response from daemon")

        response = Daemon._decoder.decode(data)
        return response

    finally:
        writer.close()
        await writer.wait_closed()


async def main():
    parser = DaemonArguments(socket_default=Daemon.DEFAULT_SOCKET_PATH)
    args = parser.parse_args(None)  # ty:ignore[possibly-missing-attribute, unresolved-attribute]

    app_config = ApplicationConfig()
    if args.config_dir:
        app_config.set_save_path(args.config_dir)
    if args.debug:
        app_config.config_path = "_test_config/"
    if args.log_terminal:
        app_config.set_log_file(None)

    daemon = Daemon(
        socket_path=args.socket, app_config=app_config, auto_load_config=True
    )

    service = "server" if args.server else "client" if args.client else None

    if not await daemon.start(service=service):
        return 1

    try:
        await daemon.wait_for_shutdown()
    except KeyboardInterrupt:
        print("\nKeyboard interrupt received")
    # Launcher handles cleanup.

    return 0


if __name__ == "__main__":
    try:
        if IS_WINDOWS:
            import winloop as asyncloop  # ty:ignore[unresolved-import, unused-ignore-comment]
        else:
            import uvloop as asyncloop  # ty:ignore[unresolved-import, unused-ignore-comment]

        asyncloop.run(main())
    except ImportError:
        asyncio.run(main())
