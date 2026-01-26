"""
Daemon service for managing lifecycle.

This module provides a daemon service that can run independently from a GUI,
managing both Client and Server services through a command socket interface.
The daemon exposes a Unix socket (Linux/macOS) or TCP socket on localhost (Windows)
for receiving commands to control the application.
"""

import asyncio
import datetime
import errno
import json
import os

from os import path
import signal
import socket
import sys
from typing import Optional, Dict, Any, Callable
from enum import StrEnum

from config import ApplicationConfig, ServerConfig, ClientConfig
from service.client import Client
from service.server import Server
from utils import UIDGenerator
from utils.logging import Logger, get_logger
from event.notification import (
    NotificationManager,
    NotificationEvent,
    OtpGeneratedEvent,
    InfoEvent,
    ErrorEvent,
)


# Determine platform for socket type
IS_WINDOWS = sys.platform in ("win32", "cygwin", "cli")


class DaemonException(Exception):
    """Base exception for daemon errors"""

    pass


class DaemonAlreadyRunningException(DaemonException):
    """Exception raised when daemon is already running (socket/port already in use)"""

    pass


class DaemonPortOccupiedException(DaemonException):
    """Exception raised when TCP port is occupied by another process"""

    pass


class DaemonCommand(StrEnum):
    """Available daemon commands"""

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

    # SSL/Certificate management
    ENABLE_SSL = "enable_ssl"
    DISABLE_SSL = "disable_ssl"
    SHARE_CERTIFICATE = "share_certificate"
    RECEIVE_CERTIFICATE = "receive_certificate"
    SET_OTP = "set_otp"

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

    def __init__(self, params: Optional[Dict[str, Any]] = None):
        # super().__init__()
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
        self.start_datetime = None  # Start timestamp

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


class Daemon:
    """
    Main daemon class for managing lifecycle.

    This daemon runs independently and provides a command socket interface
    for controlling Client and Server services, as well as their configurations.
    Supports both Unix sockets (Linux/macOS) and TCP sockets on localhost (Windows).

    Example:
        # Create and start daemon (Unix)
        daemon = Daemon(
            socket_path="/tmp/temp.sock",
            app_config=ApplicationConfig()
        )

        # Create and start daemon (Windows)
        daemon = Daemon(
            socket_path="127.0.0.1:65654",
            app_config=ApplicationConfig()
        )

        await daemon.start()

        # Daemon will run until shutdown command is received
        await daemon.wait_for_shutdown()

        # Cleanup
        await daemon.stop()
    """

    # Platform-specific default paths
    # On Windows, use TCP socket on localhost instead of named pipes for better asyncio compatibility
    if IS_WINDOWS:
        DEFAULT_SOCKET_PATH = f"127.0.0.1:{ApplicationConfig.DEFAULT_DAEMON_PORT}"
    else:
        DEFAULT_SOCKET_PATH: str = path.join(
            ApplicationConfig.get_main_path(), ApplicationConfig.DEFAULT_UNIX_SOCK_NAME
        )

    MAX_CONNECTIONS = 1  # Only accept one connection at a time
    BUFFER_SIZE = 16384  # 16KB for larger responses

    def __init__(
        self,
        socket_path: Optional[str] = None,
        app_config: Optional[ApplicationConfig] = None,
        auto_load_config: bool = True,
    ):
        """
        Initialize the daemon.

        Args:
            socket_path: Path to Unix socket or TCP address:port (e.g., "127.0.0.1:65655") for command interface
            app_config: Application configuration
            auto_load_config: Whether to auto-load existing configurations
        """
        self.socket_path = socket_path or self.DEFAULT_SOCKET_PATH
        self.app_config = app_config or ApplicationConfig()
        self.auto_load_config = auto_load_config

        # Initialize logging with file output
        self._logger = get_logger(
            self.__class__.__name__,
            level=Logger.INFO,
            is_root=True,
            log_file=self.app_config.get_default_log_file(),
        )

        # Service instances
        self._server: Optional[Server] = None
        self._client: Optional[Client] = None
        self._state: Dict[str, RunningState] = {
            "server": RunningState("server", False),
            "client": RunningState("client", False),
        }

        # Configurations
        self._server_config: Optional[ServerConfig] = None
        self._client_config: Optional[ClientConfig] = None

        # Notification manager for event broadcasting
        self._notification_manager = NotificationManager(
            callback=self._send_notification
        )

        # Daemon state
        self._running = False
        self._shutdown_event = asyncio.Event()
        self._socket_server: Optional[asyncio.AbstractServer] = None

        # Connected client management (only one at a time)
        self._connected_client_reader: Optional[asyncio.StreamReader] = None
        self._connected_client_writer: Optional[asyncio.StreamWriter] = None
        self._client_connection_lock = asyncio.Lock()

        # Command handlers registry
        self._command_handlers: Dict[str, Callable] = {
            DaemonCommand.SERVICE_CHOICE: self._handle_service_choice,
            DaemonCommand.START_SERVER: self._handle_start_server,
            DaemonCommand.STOP_SERVER: self._handle_stop_server,
            DaemonCommand.START_CLIENT: self._handle_start_client,
            DaemonCommand.STOP_CLIENT: self._handle_stop_client,
            DaemonCommand.STATUS: self._handle_status,
            DaemonCommand.SERVER_STATUS: self._handle_server_status,
            DaemonCommand.CLIENT_STATUS: self._handle_client_status,
            DaemonCommand.GET_SERVER_CONFIG: self._handle_get_server_config,
            DaemonCommand.SET_SERVER_CONFIG: self._handle_set_server_config,
            DaemonCommand.GET_CLIENT_CONFIG: self._handle_get_client_config,
            DaemonCommand.SET_CLIENT_CONFIG: self._handle_set_client_config,
            DaemonCommand.SAVE_CONFIG: self._handle_save_config,
            DaemonCommand.RELOAD_CONFIG: self._handle_reload_config,
            DaemonCommand.ENABLE_STREAM: self._handle_enable_stream,
            DaemonCommand.DISABLE_STREAM: self._handle_disable_stream,
            DaemonCommand.GET_STREAMS: self._handle_get_streams,
            DaemonCommand.ADD_CLIENT: self._handle_add_client,
            DaemonCommand.REMOVE_CLIENT: self._handle_remove_client,
            DaemonCommand.EDIT_CLIENT: self._handle_edit_client,
            DaemonCommand.LIST_CLIENTS: self._handle_list_clients,
            DaemonCommand.ENABLE_SSL: self._handle_enable_ssl,
            DaemonCommand.DISABLE_SSL: self._handle_disable_ssl,
            DaemonCommand.SHARE_CERTIFICATE: self._handle_share_certificate,
            DaemonCommand.RECEIVE_CERTIFICATE: self._handle_receive_certificate,
            DaemonCommand.SET_OTP: self._handle_set_otp,
            DaemonCommand.CHECK_SERVER_CHOICE_NEEDED: self._handle_check_server_choice_needed,
            DaemonCommand.GET_FOUND_SERVERS: self._handle_get_found_servers,
            DaemonCommand.CHOOSE_SERVER: self._handle_choose_server,
            DaemonCommand.CHECK_OTP_NEEDED: self._handle_check_otp_needed,
            DaemonCommand.DISCOVER_SERVICES: self._get_discovered_services,
            DaemonCommand.SHUTDOWN: self._handle_shutdown,
            DaemonCommand.PING: self._handle_ping,
        }

        # Setup signal handlers for graceful shutdown
        self._setup_signal_handlers()

    def _setup_signal_handlers(self):
        """Setup signal handlers for graceful shutdown"""
        if not IS_WINDOWS:  # Unix signals
            for sig in (signal.SIGTERM, signal.SIGINT):
                try:
                    asyncio.get_event_loop().add_signal_handler(
                        sig, lambda: asyncio.create_task(self._signal_shutdown())
                    )  # type: ignore
                except NotImplementedError:
                    pass

    async def _signal_shutdown(self):
        """Handle shutdown signals"""
        self._logger.info("Received shutdown signal")
        await self.stop()

    # ==================== Lifecycle Methods ====================

    def _pre_configure(self):
        """Pre-configuration steps before starting services"""
        # Check if server/client configs have missing important fields like hostname
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
                    self._logger.error(f"Preconfiguration error -> {e}")
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
                        self._logger.error(f"Preconfiguration error -> {e}")

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
                    self._logger.error(f"Preconfiguration error -> {e}")

    async def start(self) -> bool:
        """
        Start the daemon and command socket server.

        Returns:
            True if daemon started successfully, False otherwise

        Raises:
            DaemonAlreadyRunningException: If daemon is already running
            DaemonPortOccupiedException: If TCP port is occupied (Windows only)
        """
        if self._running:
            self._logger.warning("Daemon already running")
            return False

        self._logger.info("Starting Daemon...")

        # Initialize configurations
        self._server_config = ServerConfig(self.app_config)
        self._client_config = ClientConfig(self.app_config)

        if self.auto_load_config:
            try:
                await self._server_config.load()
                await self._client_config.load()
            except Exception as e:
                self._logger.warning(f"Could not load configurations -> {e}")

        self._pre_configure()

        # Create socket server based on platform
        try:
            if IS_WINDOWS:
                # Windows TCP socket (localhost only for security)
                await self._start_tcp_server()
            else:
                # Unix socket
                await self._start_unix_server()

            self._running = True
            self._logger.info(f"Daemon started, listening on {self.socket_path}")
            return True

        except (DaemonAlreadyRunningException, DaemonPortOccupiedException):
            # Re-raise daemon-specific exceptions
            raise

        except Exception as e:
            self._logger.error(f"Failed to start daemon -> {e}")
            return False

    async def _start_unix_server(self):
        """
        Start Unix socket server (Linux/macOS)

        Logic:
        - If socket exists and is connectable -> raise DaemonAlreadyRunningException
        - If socket exists but not connectable -> remove and create new one
        - If socket doesn't exist -> create new one
        """
        if os.path.exists(self.socket_path):
            self._logger.info(
                f"Socket file {self.socket_path} already exists, checking if daemon is running..."
            )

            # Try to connect to existing socket
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_unix_connection(self.socket_path), timeout=2.0
                )
                # Connection successful -> daemon is already running
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
                # Socket exists but daemon not running -> remove stale socket
                self._logger.warning(
                    f"Socket exists but daemon not running (error: {type(e).__name__}). "
                    f"Removing stale socket file..."
                )
                try:
                    os.unlink(self.socket_path)
                    self._logger.info(f"Removed stale socket file {self.socket_path}")
                except Exception as remove_error:
                    self._logger.error(f"Failed to remove stale socket: {remove_error}")
                    raise
            except OSError as e:
                if isinstance(e, OSError):
                    if e.errno == errno.ENOTSOCK:
                        # Not a socket file, remove it
                        self._logger.warning(
                            f"File {self.socket_path} is not a socket. Removing it..."
                        )
                        try:
                            os.unlink(self.socket_path)
                            self._logger.info(
                                f"Removed non-socket file {self.socket_path}"
                            )
                        except Exception as remove_error:
                            self._logger.error(
                                f"Failed to remove non-socket file: {remove_error}"
                            )
                            raise

        # Create new Unix socket server
        self._socket_server = await asyncio.start_unix_server(
            self._handle_client_connection, path=self.socket_path
        )

        # Set socket permissions (owner read/write only)
        os.chmod(self.socket_path, 0o600)
        self._logger.info(f"Unix socket server created at {self.socket_path}")

    async def _start_tcp_server(self):
        """
        Start TCP socket server on localhost (Windows)

        Logic:
        - Try to connect to the port first
        - If connection succeeds -> raise DaemonAlreadyRunningException
        - If connection fails with "connection refused" -> port is free, create server
        - If binding fails with "address already in use" -> raise DaemonPortOccupiedException
        """
        # Parse host and port from socket_path
        if ":" in self.socket_path:
            host, port_str = self.socket_path.split(":", 1)
            port = int(port_str)
        else:
            # Default fallback
            host = "127.0.0.1"
            port = ApplicationConfig.DEFAULT_PORT - 1

        self._logger.info(f"Checking if daemon is already running on {host}:{port}...")

        # First, try to connect to check if daemon is already running
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=2.0
            )
            # Connection successful -> daemon is already running
            writer.close()
            await writer.wait_closed()

            error_msg = f"Daemon is already running on {host}:{port}"
            self._logger.error(error_msg)
            raise DaemonAlreadyRunningException(error_msg)

        except (ConnectionRefusedError, OSError) as e:
            # Connection refused is expected if no daemon is running
            self._logger.debug(f"Port check failed as expected: {type(e).__name__}")

        except asyncio.TimeoutError:
            # Timeout might indicate port is open but not responding properly
            self._logger.warning(
                "Connection attempt timed out, proceeding with server creation"
            )

        # Now try to create the server
        try:
            self._socket_server = await asyncio.start_server(
                self._handle_client_connection, host=host, port=port
            )
            self._logger.info(f"TCP server started on {host}:{port}")

        except OSError as e:
            # Check if error is due to address already in use
            if (
                e.errno == 48 or "address already in use" in str(e).lower()
            ):  # errno 48 on macOS, 98 on Linux
                error_msg = (
                    f"Port {port} is already occupied by another process. "
                    f"Cannot start daemon."
                )
                self._logger.error(error_msg)
                raise DaemonPortOccupiedException(error_msg) from e
            else:
                # Other OS error
                self._logger.error(f"Failed to start TCP server: {e}")
                raise

    async def stop(self):
        """Stop the daemon and cleanup resources"""
        if not self._running:
            self._logger.warning("Daemon not running")
            return

        self._logger.info("Stopping daemon...")

        self._running = False

        # Disconnect connected client
        async with self._client_connection_lock:
            if self._connected_client_writer is not None:
                try:
                    # Send shutdown notification
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

        # Stop services
        if self._server:
            await self._server.stop()
            self._server = None

        if self._client:
            await self._client.stop()
            self._client = None

        # Close socket server
        if self._socket_server:
            self._socket_server.close()
            await self._socket_server.wait_closed()

        # Remove socket file (Unix only)
        if not IS_WINDOWS and os.path.exists(self.socket_path):
            os.unlink(self.socket_path)

        self._shutdown_event.set()
        self._logger.info("Daemon stopped")

        # Force exit to ensure all tasks are cleaned up
        os._exit(0)

    async def wait_for_shutdown(self):
        """Wait until daemon is shutdown"""
        await self._shutdown_event.wait()

    # ==================== Command Socket Handler ====================

    async def _handle_client_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ):
        """
        Handle incoming client connection on command socket.
        Only one connection is allowed at a time. The connection remains open
        and continuously listens for commands.

        Args:
            reader: Stream reader for receiving data
            writer: Stream writer for sending responses
        """
        if IS_WINDOWS:
            addr = writer.get_extra_info("peername")
            self._logger.info(f"New connection attempt from {addr}")
        else:
            addr = writer.get_extra_info("peername") or "local"
            self._logger.info("New connection attempt")

        # Check if another client is already connected
        async with self._client_connection_lock:
            if self._connected_client_writer is not None:
                self._logger.warning(
                    f"Rejecting connection from {addr}: another client already connected"
                )
                try:
                    error_response = ErrorEvent(
                        error="Another client is already connected. Only one connection is allowed at a time."
                    )
                    writer.write(self.prepare_msg_bytes(error_response))
                    await writer.drain()
                finally:
                    if writer:
                        writer.close()
                        await writer.wait_closed()
                return

            # Accept the connection
            self._connected_client_writer = writer
            self._connected_client_reader = reader
            self._logger.info(f"Client connected from {addr}")

        try:
            # Send welcome message
            welcome = InfoEvent(
                info="Connected to daemon", version=ApplicationConfig.version
            )
            await self._send_to_client(welcome)

            buff = bytearray()
            # Continuously listen for commands
            while self._running and not reader.at_eof():
                try:
                    data = await asyncio.wait_for(
                        reader.read(self.BUFFER_SIZE), timeout=1.0
                    )

                    if not data:
                        # self._logger.info("Client disconnected (no data)")
                        await asyncio.sleep(0.1)
                        continue

                    buff.extend(data)

                    if len(buff) == 0:
                        await asyncio.sleep(0.1)
                        continue

                    commands_data, bytes_read = self.parse_msg_bytes(bytes(buff))

                    # Clear read bytes from buffer
                    if bytes_read > 0:
                        buff = buff[bytes_read:]

                    for command_data in commands_data:
                        try:
                            command = command_data.get("command")
                            if not isinstance(command, str):
                                raise ValueError("Missing or invalid 'command' field")
                            params = command_data.get("params", {})
                        except json.JSONDecodeError as e:
                            response = ErrorEvent(error=f"Invalid JSON -> {e}")
                            await self._send_to_client(response)
                            continue

                        # Execute command (no response needed, commands send notifications)
                        asyncio.create_task(self._execute_command(command, params))
                        await asyncio.sleep(0)

                except asyncio.TimeoutError:
                    # Timeout is normal, just check running state and continue
                    await asyncio.sleep(0)
                    continue
                except (
                    BrokenPipeError,
                    ConnectionResetError,
                    ConnectionAbortedError,
                ) as e:
                    self._logger.error(f"Client disconnected ({e})")
                    break
                except Exception as e:
                    self._logger.error(f"{e}")
                    response = ErrorEvent(error=f"Internal error -> {e}")
                    try:
                        await self._send_to_client(response)
                    except Exception:
                        break
                    await asyncio.sleep(0.5)

        except Exception as e:
            self._logger.error(f"{e}")

        finally:
            # Cleanup connection
            async with self._client_connection_lock:
                if self._connected_client_writer == writer:
                    self._connected_client_reader = None
                    self._connected_client_writer = None

            self._logger.info(f"Client {addr} disconnected")
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    @staticmethod
    def prepare_msg_bytes(data: NotificationEvent | dict) -> bytes:
        """
        Prepare message bytes for sending to client.

        Args:
            data: NotificationEvent or dict to send

        Returns:
            Encoded bytes with length prefix and newline delimiter
        """
        if isinstance(data, dict):
            r = json.dumps(data)
        else:
            r = data.to_json()
        message_bytes = r.encode("utf-8")
        length_prefix = len(message_bytes).to_bytes(4, byteorder="big")
        return message_bytes + length_prefix + b"\n"

    @staticmethod
    def parse_msg_bytes(data: bytes) -> tuple[list[dict], int]:
        """
        Parses a byte sequence containing serialized messages with length prefixes and a delimiter.

        Args:
            data: A sequence of bytes containing the serialized messages.

        Returns:
            list[dict]: A list of Python dictionaries representing the parsed JSON messages.
            offset: The number of bytes consumed from the input data.

        Raises:
            ValueError: If the byte sequence contains incomplete length prefixes, lacks message
                delimiters, contains invalid JSON data, or suffers from other structural
                inconsistencies in the sequence.
        """
        offset = 0
        d_len = len(data)
        try:
            if d_len > 5:
                lines = []
                while offset < d_len - 5:
                    if offset + 4 > d_len:
                        raise ValueError("Incomplete length prefix")
                    # Find first \n index
                    idx = data.find(b"\n", offset)
                    if idx == -1:
                        # Wait for more data
                        # print(f"No delimiter found, stopping parse at offset {offset}")
                        # print(f"Data: {data[offset:]}")
                        break
                    length_bytes = data[idx - 4 : idx]
                    msg_length = int.from_bytes(length_bytes, byteorder="big")
                    msg_data = data[offset : offset + msg_length]
                    message_str = msg_data.decode("utf-8").strip()
                    lines.append(json.loads(message_str))
                    offset += msg_length + 5  # Move past message and delimiter
                return lines, offset
            else:
                return [], 0
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON data -> {e}")
        except ValueError:
            raise

    async def _send_to_client(self, event: NotificationEvent) -> bool:
        """
        Send notification event to the connected client.
        This method can be called from anywhere to push events to the client.

        Args:
            event: NotificationEvent to send

        Returns:
            True if sent successfully, False otherwise
        """
        async with self._client_connection_lock:
            if self._connected_client_writer is None:
                self._logger.warning("No client connected, cannot send notification")
                return False

            try:
                self._connected_client_writer.write(self.prepare_msg_bytes(event))
                await self._connected_client_writer.drain()
                return True
            except Exception as e:
                self._logger.error(f"{e}")
                # Connection broken, clear it
                self._connected_client_reader = None
                self._connected_client_writer = None
                return False

    async def _send_notification(self, event: NotificationEvent) -> None:
        """
        Internal callback for notification manager to send events to connected client.

        Args:
            event: NotificationEvent to send
        """
        if not self.is_client_connected():
            return

        await self._send_to_client(event)

    async def _service_notification_callback(self, event: NotificationEvent) -> None:
        """
        Callback for service (Client/Server) to send notifications.
        This bridges service events to the notification manager.

        Args:
            event: NotificationEvent from service
        """
        # Services now send NotificationEvent objects directly
        # We forward them through the notification manager
        asyncio.create_task(self._notification_manager.notify_event(event))

    def is_client_connected(self) -> bool:
        """Check if a client is currently connected"""
        return self._connected_client_writer is not None

    def _get_active_service(
        self, service_type: str = "auto"
    ) -> tuple[Optional[Any], str, Optional[str]]:
        """
        Get the active service based on service_type parameter.

        Args:
            service_type: "auto", "server", or "client"

        Returns:
            Tuple of (service instance, service name, error message if any)
        """
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
        """
        Get the active service and its config.

        Args:
            service_type: "auto", "server", or "client"

        Returns:
            Tuple of (service instance, config, service name, error message if any)
        """
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

    async def _execute_command(self, command: str, params: Dict[str, Any]) -> None:
        """
        Execute a daemon command.

        Args:
            command: Command to execute
            params: Command parameters
        """
        # Check if command exists
        handler = self._command_handlers.get(command)
        if not handler:
            await self._notification_manager.notify_error(
                f"Unknown command: {command}", data={"command": command}
            )
            return

        # Execute handler
        try:
            await handler(params)
        except Exception as e:
            self._logger.error(f"{command} -> {e}")
            await self._notification_manager.notify_error(
                f"Command execution failed: {str(e)}",
                data={"command": command, "error": str(e)},
            )

    # ==================== Command Handlers: Service Control ====================

    async def _handle_service_choice(self, params: Dict[str, Any]) -> None:
        """Handle service choice between client and server"""
        choice = params.get("service")
        command = DaemonCommand.SERVICE_CHOICE.value

        if choice == "server":
            if not self._server:
                self._server = Server(
                    app_config=self.app_config,
                    server_config=self._server_config,
                    auto_load_config=False,  # Already loaded
                )
                # Connect notification callback
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
            if not self._client:
                self._client = Client(
                    app_config=self.app_config,
                    client_config=self._client_config,
                    auto_load_config=False,  # Already loaded
                )
                # Connect notification callback
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

    async def _handle_start_server(self, params: Dict[str, Any]) -> None:
        """Start the server service"""
        command = DaemonCommand.START_SERVER.value

        if self._server and self._server.is_running():
            await self._notification_manager.notify_command_error(
                command, "Server already running"
            )
            return

        # Check if client is running (mutual exclusion)
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

            # # Notify starting
            # await self._notification_manager.notify_service_started(
            #     "Server", data={"status": "starting"}
            # )

            success = await self._server.start()
            if success:
                self._state["server"].start()
                response_data = {
                    "host": self._server.config.host,
                    "port": self._server.config.port,
                    "start_time": self._state["server"].get_timestamp(),
                    "enabled_streams": self._server.get_enabled_streams(),
                }
                self._logger.set_level(self._server.config.log_level)

                await self._notification_manager.notify_command_success(
                    command, "Server started successfully", result_data=response_data
                )
            else:
                await self._notification_manager.notify_command_error(
                    command, "Failed to start server"
                )
        except Exception as e:
            self._logger.error(f"{e}")
            await self._notification_manager.notify_command_error(command, f"{str(e)}")

    async def _handle_stop_server(self, params: Dict[str, Any]) -> None:
        """Stop the server service"""
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

    async def _handle_start_client(self, params: Dict[str, Any]) -> None:
        """Start the client service"""
        command = DaemonCommand.START_CLIENT.value

        if self._client and self._client.is_running():
            await self._notification_manager.notify_command_error(
                command, "Client already running"
            )
            return

        # Check if server is running (mutual exclusion)
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
            start_task = asyncio.create_task(self._client.start())

            success = await start_task
            if success:
                self._state["client"].start()
                response_data = {
                    **self._client.config.server_info.to_dict(),
                    "start_time": self._state["client"].get_timestamp(),
                    "enabled_streams": self._client.get_enabled_streams(),
                }
                self._logger.set_level(self._client.config.log_level)

                await self._notification_manager.notify_command_success(
                    command, "Client started successfully", result_data=response_data
                )
            else:
                await self._notification_manager.notify_command_error(
                    command, "Failed to start client"
                )
        except Exception as e:
            self._logger.error(f"{e}")
            await self._notification_manager.notify_command_error(command, f"{str(e)}")

    async def _handle_stop_client(self, params: Dict[str, Any]) -> None:
        """Stop the client service"""
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

    # ==================== Command Handlers: Status ====================

    async def _handle_status(self, params: Dict[str, Any]) -> None:
        """Get overall daemon status"""
        command = DaemonCommand.STATUS.value

        status = {
            "platform": "windows" if IS_WINDOWS else "unix",
            "socket_path": self.socket_path,
            "client_connected": self.is_client_connected(),
        }

        if self._server_config and self._server:
            status["server_info"] = {  # type: ignore
                **self._server_config.to_dict(),
                "running": self._server.is_running(),
                "start_time": self._state["server"].get_timestamp(),
            }

        if self._client_config and self._client:
            status["client_info"] = {  # type: ignore
                **self._client_config.to_dict(),
                "running": self._client.is_running(),
                "connected": self._client.is_connected(),
                "start_time": self._state["client"].get_timestamp(),
                "otp_nededed": await self._client.otp_needed(),
                "service_choice_needed": await self._client.server_choice_needed(),
            }

            if await self._client.server_choice_needed():
                status["client_info"]["available_servers"] = [
                    s.as_dict() for s in self._client.get_found_servers()
                ]

        await self._notification_manager.notify_command_success(
            command, "Status retrieved", result_data=status
        )

    async def _handle_server_status(self, params: Dict[str, Any]) -> None:
        """Get server status"""
        command = DaemonCommand.SERVER_STATUS.value

        if not self._server:
            await self._notification_manager.notify_command_success(
                command, "Server status", result_data={"running": False}
            )
            return

        running = self._server.is_running()
        status = {"running": running}

        if running:
            connected_clients = self._server.clients_manager.get_clients()
            registered_clients = self._server.get_clients()

            status.update(
                {  # type: ignore
                    "host": self._server.config.host,
                    "port": self._server.config.port,
                    "connected_clients": len(connected_clients),
                    "registered_clients": len(registered_clients),
                    "enabled_streams": self._server.get_enabled_streams(),
                    "active_streams": self._server.get_active_streams(),
                    "ssl_enabled": self._server.config.ssl_enabled,
                }
            )

        await self._notification_manager.notify_command_success(
            command, "Server status retrieved", result_data=status
        )

    async def _handle_client_status(self, params: Dict[str, Any]) -> None:
        """Get client status"""
        command = DaemonCommand.CLIENT_STATUS.value

        if not self._client:
            await self._notification_manager.notify_command_success(
                command, "Client status", result_data={"running": False}
            )
            return

        running = self._client.is_running()
        status = {"running": running}

        if running:
            status.update(
                {
                    "server_host": self._client.config.get_server_host(),
                    "server_port": self._client.config.get_server_port(),
                    "connected": self._client.is_connected(),
                    "enabled_streams": self._client.get_enabled_streams(),
                    "active_streams": self._client.get_active_streams(),
                    "ssl_enabled": self._client.config.ssl_enabled,
                    "has_certificate": self._client.has_certificate(),
                    "auto_reconnect": self._client.config.do_auto_reconnect(),
                }
            )

        await self._notification_manager.notify_command_success(
            command, "Client status retrieved", result_data=status
        )

    # ==================== Command Handlers: Configuration ====================

    async def _handle_get_server_config(self, params: Dict[str, Any]) -> None:
        """Get server configuration"""
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

    async def _handle_set_server_config(self, params: Dict[str, Any]) -> None:
        """Set server configuration"""
        command = DaemonCommand.SET_SERVER_CONFIG.value

        if not self._server_config:
            await self._notification_manager.notify_command_error(
                command, "Server configuration not initialized"
            )
            return

        try:
            if "uid" in params:
                self._server_config.uid = params.get("uid")

            # Update configuration
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

            # Silently try to save config
            if self._server:
                await self._server.save_config()

            await self._notification_manager.notify_command_success(
                command, "Server configuration updated"
            )
        except Exception as e:
            await self._notification_manager.notify_command_error(command, f"{str(e)}")

    async def _handle_get_client_config(self, params: Dict[str, Any]) -> None:
        """Get client configuration"""
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

    async def _handle_set_client_config(self, params: Dict[str, Any]) -> None:
        """Set client configuration"""
        command = DaemonCommand.SET_CLIENT_CONFIG.value

        if not self._client_config:
            await self._notification_manager.notify_command_error(
                command, "Client configuration not initialized"
            )
            return

        try:
            # Update configuration
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

                if host == "" and hostname == "":  # Clear server connection
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

    async def _handle_save_config(self, params: Dict[str, Any]) -> None:
        """Save configurations to disk"""
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

    async def _handle_reload_config(self, params: Dict[str, Any]) -> None:
        """Reload configurations from disk"""
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

    # ==================== Command Handlers: Stream Management ====================

    async def _handle_enable_stream(self, params: Dict[str, Any]) -> None:
        """Enable a stream on running service"""
        command = DaemonCommand.ENABLE_STREAM.value
        stream_type = params.get("stream_type")
        service_type = params.get("service", "auto")  # "server", "client", or "auto"

        if stream_type is None:
            await self._notification_manager.notify_command_error(
                command, "Missing 'stream_type' parameter"
            )
            return

        try:
            # Convert string to StreamType if needed
            if isinstance(stream_type, str):
                stream_type = int(stream_type)

            # Determine which service to use
            service, service_name, error = self._get_active_service(service_type)
            if error:
                await self._notification_manager.notify_command_error(command, error)
                return

            if not service:
                await self._notification_manager.notify_command_error(
                    command, "No active service to enable stream on"
                )
                return

            # Enable stream
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

    async def _handle_disable_stream(self, params: Dict[str, Any]) -> None:
        """Disable a stream on running service"""
        command = DaemonCommand.DISABLE_STREAM.value
        stream_type = params.get("stream_type")
        service_type = params.get("service", "auto")

        if stream_type is None:
            await self._notification_manager.notify_command_error(
                command, "Missing 'stream_type' parameter"
            )
            return

        try:
            # Convert string to StreamType if needed
            if isinstance(stream_type, str):
                stream_type = int(stream_type)

            # Determine which service to use
            service, service_name, error = self._get_active_service(service_type)
            if error:
                await self._notification_manager.notify_command_error(command, error)
                return

            if not service:
                await self._notification_manager.notify_command_error(
                    command, "No active service to disable stream on"
                )
                return

            # Disable stream
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

    async def _handle_get_streams(self, params: Dict[str, Any]) -> None:
        """Get stream information"""
        command = DaemonCommand.GET_STREAMS.value
        service_type = params.get("service", "auto")

        # Determine which service to use
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

    # ==================== Command Handlers: Client Management (Server) ====================

    async def _handle_add_client(self, params: Dict[str, Any]) -> None:
        """Add a client to server (server only)"""
        command = DaemonCommand.ADD_CLIENT.value

        if not self._server:
            await self._notification_manager.notify_command_error(
                command, "Server is not running"
            )
            return

        try:
            hostname = params.get("hostname")
            ip_address = params.get("ip_address")
            screen_position = params.get("screen_position")

            if not hostname and not ip_address:
                await self._notification_manager.notify_command_error(
                    command, "Must provide either hostname or ip_address"
                )
                return

            if not screen_position:
                await self._notification_manager.notify_command_error(
                    command, "Must provide screen_position"
                )
                return

            await self._server.add_client(
                hostname=hostname,
                ip_address=ip_address,
                screen_position=screen_position,
            )

            await self._notification_manager.notify_command_success(
                command,
                f"Client added at position {screen_position}",
                result_data={
                    "hostname": hostname,
                    "ip_address": ip_address,
                    "screen_position": screen_position,
                },
            )
        except Exception as e:
            await self._notification_manager.notify_command_error(command, f"{str(e)}")

    async def _handle_remove_client(self, params: Dict[str, Any]) -> None:
        """Remove a client from server (server only)"""
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

    async def _handle_edit_client(self, params: Dict[str, Any]) -> None:
        """Edit a client configuration (server only)"""
        command = DaemonCommand.EDIT_CLIENT.value

        if not self._server or not self._server.is_running():
            await self._notification_manager.notify_command_error(
                command, "Server is not running"
            )
            return

        try:
            hostname = params.get("hostname")
            ip_address = params.get("ip_address")
            new_screen_position = params.get("new_screen_position")

            if not hostname and not ip_address:
                await self._notification_manager.notify_command_error(
                    command, "Must provide either hostname or ip_address"
                )
                return

            if not new_screen_position:
                await self._notification_manager.notify_command_error(
                    command, "Must provide new_screen_position"
                )
                return

            await self._server.edit_client(
                hostname=hostname,
                ip_address=ip_address,
                new_screen_position=new_screen_position,
            )

            await self._notification_manager.notify_command_success(
                command,
                f"Client updated to position {new_screen_position}",
                result_data={
                    "hostname": hostname,
                    "ip_address": ip_address,
                    "new_screen_position": new_screen_position,
                },
            )
        except Exception as e:
            await self._notification_manager.notify_command_error(command, f"{str(e)}")

    async def _handle_list_clients(self, params: Dict[str, Any]) -> None:
        """List registered clients (server only)"""
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
                        "ip_address": client.ip_address,
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

    # ==================== Command Handlers: SSL/Certificate ====================

    async def _handle_enable_ssl(self, params: Dict[str, Any]) -> None:
        """Enable SSL"""
        command = DaemonCommand.ENABLE_SSL.value
        service_type = params.get("service", "auto")

        # Determine which service
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

    async def _handle_disable_ssl(self, params: Dict[str, Any]) -> None:
        """Disable SSL"""
        command = DaemonCommand.DISABLE_SSL.value
        service_type = params.get("service", "auto")

        # Determine which service
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

    async def _handle_share_certificate(self, params: Dict[str, Any]) -> None:
        """Share certificate (server only)"""
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
            # Send notification
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

    async def _handle_receive_certificate(self, params: Dict[str, Any]) -> None:
        """Receive certificate (client only)"""
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

    # ==================== Command Handlers: Server Selection & OTP ====================

    async def _handle_check_server_choice_needed(self, params: Dict[str, Any]) -> None:
        """Check if server choice is needed (client only)"""
        command = DaemonCommand.CHECK_SERVER_CHOICE_NEEDED.value

        if not self._client:
            await self._notification_manager.notify_command_error(
                command, "Client not initialized"
            )
            return

        try:
            # Check if we're currently waiting for server choice
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

    async def _handle_get_found_servers(self, params: Dict[str, Any]) -> None:
        """Get list of found servers (client only)"""
        command = DaemonCommand.GET_FOUND_SERVERS.value

        if not self._client:
            await self._notification_manager.notify_command_error(
                command, "Client not initialized"
            )
            return

        try:
            servers = self._client.get_found_servers()

            # Convert services to dict format
            servers_data = []
            for s in servers:
                servers_data.append(s.as_dict())

            await self._notification_manager.notify_command_success(
                command,
                "Found servers retrieved",
                result_data={"servers": servers_data, "count": len(servers_data)},
            )
        except Exception as e:
            await self._notification_manager.notify_command_error(command, f"{str(e)}")

    async def _handle_choose_server(self, params: Dict[str, Any]) -> None:
        """Choose a server from found servers (client only)"""
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

            # Choose the server
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

    async def _handle_check_otp_needed(self, params: Dict[str, Any]) -> None:
        """Check if OTP is needed for certificate (client only)"""
        command = DaemonCommand.CHECK_OTP_NEEDED.value

        if not self._client:
            await self._notification_manager.notify_command_error(
                command, "Client not initialized"
            )
            return

        try:
            # Check if we're currently waiting for OTP
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

    async def _handle_set_otp(self, params: Dict[str, Any]) -> None:
        """Set OTP for certificate reception (client only)"""
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

    # ==================== Command Handlers: Service Discovery ====================

    async def _get_discovered_services(self, params: Dict[str, Any]) -> None:
        """Get available services on network"""
        command = DaemonCommand.DISCOVER_SERVICES.value

        try:
            # Use service discovery from client if available
            if self._client:
                services = self._client.get_found_servers()

                # Convert services to dict format
                services_data = []
                for s in services:
                    services_data.append(
                        {
                            "name": s.name,
                            "address": s.address,
                            "port": s.port,
                            "hostname": s.hostname,
                            "uid": s.uid,
                        }
                    )

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

    # ==================== Command Handlers: Daemon Control ====================

    async def _handle_shutdown(self, params: Dict[str, Any]) -> None:
        """Shutdown the daemon"""
        command = DaemonCommand.SHUTDOWN.value

        # Send success notification before shutdown
        await self._notification_manager.notify_command_success(
            command, "Daemon shutting down..."
        )

        # Schedule shutdown after notification is sent
        asyncio.create_task(self._delayed_shutdown())

    async def _delayed_shutdown(self):
        """Delay shutdown to allow response to be sent"""
        await asyncio.sleep(0.5)
        await self.stop()

    async def _handle_ping(self, params: Dict[str, Any]) -> None:
        """Simple ping command to check daemon is alive"""
        await self._notification_manager.notify_pong()

    # ==================== Utility Methods ====================

    def is_running(self) -> bool:
        """Check if daemon is running"""
        return self._running

    def get_socket_path(self) -> str:
        """Get the daemon socket path"""
        return self.socket_path


# ==================== Helper Functions ====================


async def send_daemon_command(
    command: str,
    params: Optional[Dict[str, Any]] = None,
    socket_path: Optional[str] = None,
    timeout: float = 30.0,
) -> Dict[str, Any]:
    """
    Send a command to the daemon and get response.

    Args:
        command: Command to send
        params: Command parameters
        socket_path: Path to daemon socket (Unix) or host:port (TCP on Windows)
        timeout: Command timeout in seconds

    Returns:
        Response dictionary

    Raises:
        ConnectionError: If cannot connect to daemon
        TimeoutError: If command times out
    """
    socket_path = socket_path or Daemon.DEFAULT_SOCKET_PATH

    command_data = DaemonCommand.new(
        command=command, socket_path=socket_path, **(params or {})
    )

    if IS_WINDOWS or ":" in socket_path:
        # Windows TCP socket or explicit TCP address
        return await _send_tcp_command(socket_path, command_data.to_dict(), timeout)
    else:
        # Unix socket
        return await _send_unix_command(socket_path, command_data.to_dict(), timeout)


async def _send_unix_command(
    socket_path: str, command_data: dict, timeout: float
) -> dict:
    """Send command via Unix socket"""
    # Check if socket exists
    if not os.path.exists(socket_path):
        raise ConnectionError(f"Daemon not running (socket not found: {socket_path})")

    # Connect to daemon
    reader, writer = await asyncio.wait_for(
        asyncio.open_unix_connection(socket_path), timeout=5.0
    )

    try:
        # Send command
        writer.write(Daemon.prepare_msg_bytes(command_data))
        await writer.drain()

        # Read response
        data = await asyncio.wait_for(reader.read(Daemon.BUFFER_SIZE), timeout=timeout)

        if not data:
            raise ConnectionError("No response from daemon")

        response = json.loads(data.decode("utf-8"))
        return response

    finally:
        writer.close()
        await writer.wait_closed()


async def _send_tcp_command(
    socket_path: str, command_data: dict, timeout: float
) -> dict:
    """Send command via TCP socket (Windows)"""
    # Parse host and port
    if ":" in socket_path:
        host, port_str = socket_path.split(":", 1)
        port = int(port_str)
    else:
        raise ValueError(
            f"Invalid TCP socket path format: {socket_path}. Expected host:port"
        )

    # Connect to daemon
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=5.0
        )
    except (ConnectionRefusedError, OSError) as e:
        raise ConnectionError(
            f"Daemon not running (cannot connect to {host}:{port}) -> {e}"
        )

    try:
        # Send command
        writer.write(Daemon.prepare_msg_bytes(command_data))
        await writer.drain()

        # Read response
        data = await asyncio.wait_for(reader.read(Daemon.BUFFER_SIZE), timeout=timeout)

        if not data:
            raise ConnectionError("No response from daemon")

        response = json.loads(data.decode("utf-8"))
        return response

    finally:
        writer.close()
        await writer.wait_closed()


# ==================== Main Entry Point ====================


async def main():
    """Main entry point for daemon"""
    import argparse

    parser = argparse.ArgumentParser(description="Daemon")
    parser.add_argument(
        "--socket",
        default=Daemon.DEFAULT_SOCKET_PATH,
        help="Socket path (Unix socket) or host:port (TCP on Windows)",
    )
    parser.add_argument("--config-dir", help="Configuration directory path")
    parser.add_argument("--debug", action="store_true", help="Enable debug directory")
    parser.add_argument(
        "--log-terminal", action="store_true", help="Log only to stdout"
    )

    args = parser.parse_args()

    # Setup application config
    app_config = ApplicationConfig()
    if args.config_dir:
        app_config.set_save_path(args.config_dir)
    if args.debug:
        app_config.config_path = "_test_config/"
    if args.log_terminal:
        app_config.set_log_file(None)

    # Create and start daemon
    daemon = Daemon(
        socket_path=args.socket, app_config=app_config, auto_load_config=True
    )

    if not await daemon.start():
        print("Failed to start daemon")
        return 1

    print(f"Daemon started successfully on {daemon.get_socket_path()}")
    print(f"Platform: {'Windows (TCP Socket)' if IS_WINDOWS else 'Unix (Socket)'}")

    # Wait for shutdown
    try:
        await daemon.wait_for_shutdown()
    except KeyboardInterrupt:
        print("\nKeyboard interrupt received")
    finally:
        await daemon.stop()

    return 0


if __name__ == "__main__":
    try:
        if IS_WINDOWS:
            import winloop as asyncloop  # ty:ignore[unresolved-import]
        else:
            import uvloop as asyncloop  # ty:ignore[unresolved-import]

        asyncloop.run(main())
    except ImportError:
        asyncio.run(main())
