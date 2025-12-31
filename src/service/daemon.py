"""
Daemon service for managing lifecycle.

This module provides a daemon service that can run independently from a GUI,
managing both Client and Server services through a command socket interface.
The daemon exposes a Unix socket (Linux/macOS) or TCP socket on localhost (Windows)
for receiving commands to control the application.
"""

import asyncio
import errno
import json
import os
import signal
import socket
import sys
from typing import Optional, Dict, Any, Callable
from enum import Enum

from config import ApplicationConfig, ServerConfig, ClientConfig
from service.client import Client
from service.server import Server
from utils import UIDGenerator
from utils.logging import Logger, get_logger

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


class DaemonCommand(str, Enum):
    """Available daemon commands"""

    # Service control
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


class DaemonResponse:
    """Standardized daemon response format"""

    def __init__(self, success: bool, data: Any = None, error: Optional[str] = None):
        self.success = success
        self.data = data
        self.error = error

    def to_dict(self) -> Dict[str, Any]:
        return {"success": self.success, "data": self.data, "error": self.error}

    def to_json(self) -> str:
        return json.dumps(self.to_dict())


class Daemon:
    """
    Main daemon class for managing lifecycle.

    This daemon runs independently and provides a command socket interface
    for controlling Client and Server services, as well as their configurations.
    Supports both Unix sockets (Linux/macOS) and TCP sockets on localhost (Windows).

    Example:
        # Create and start daemon (Unix)
        daemon = Daemon(
            socket_path="/tmp/pycontinuity.sock",
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
        DEFAULT_SOCKET_PATH = (
            f"127.0.0.1:{ApplicationConfig.DEFAULT_PORT - 3}"  # TCP address:port
        )
    else:
        DEFAULT_SOCKET_PATH = "/tmp/pycontinuity_daemon.sock"  # Unix socket

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

        # Initialize logging
        self._logger = get_logger(
            self.__class__.__name__, level=Logger.INFO, is_root=True
        )

        # Service instances
        self._server: Optional[Server] = None
        self._client: Optional[Client] = None

        # Configurations
        self._server_config: Optional[ServerConfig] = None
        self._client_config: Optional[ClientConfig] = None

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
            if not self._client_config.get_uid():
                try:
                    key = self._client_config.get_hostname()
                    if not key:
                        key = socket.gethostname()
                        self._logger.warning(
                            "Client configuration missing hostname, setting new one",
                            new_hostname=key,
                        )
                        self._client_config.set_hostname(key)
                    new_uid = UIDGenerator.generate_uid(key)
                    self._client_config.set_uid(new_uid)
                    self._logger.info("Generated new client UID", client_uid=new_uid)
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

        self._server = Server(
            app_config=self.app_config,
            server_config=self._server_config,
            auto_load_config=False,  # Already loaded
        )

        self._client = Client(
            app_config=self.app_config,
            client_config=self._client_config,
            auto_load_config=False,  # Already loaded
        )

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
                    shutdown_msg = DaemonResponse(
                        success=True,
                        data={
                            "event": "daemon_shutdown",
                            "message": "Daemon is shutting down",
                        },
                    )
                    self._connected_client_writer.write(
                        shutdown_msg.to_json().encode("utf-8")
                    )
                    self._connected_client_writer.write(b"\n")
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
        addr = writer.get_extra_info("peername")
        self._logger.info(f"New connection attempt from {addr}")

        # Check if another client is already connected
        async with self._client_connection_lock:
            if self._connected_client_writer is not None:
                self._logger.warning(
                    f"Rejecting connection from {addr}: another client already connected"
                )
                try:
                    error_response = DaemonResponse(
                        success=False,
                        error="Another client is already connected. Only one connection is allowed at a time.",
                    )
                    writer.write(error_response.to_json().encode("utf-8"))
                    writer.write(b"\n")
                    await writer.drain()
                finally:
                    writer.close()
                    await writer.wait_closed()
                return

            # Accept the connection
            self._connected_client_reader = reader
            self._connected_client_writer = writer
            self._logger.info(f"Client connected from {addr}")

        try:
            # Send welcome message
            welcome = DaemonResponse(
                success=True,
                data={
                    "message": "Connected",
                    "version": ApplicationConfig.version,
                },
            )
            await self._send_to_client(welcome)

            # Continuously listen for commands
            while self._running and not reader.at_eof():
                try:
                    data = await asyncio.wait_for(
                        reader.read(self.BUFFER_SIZE), timeout=1.0
                    )

                    if not data:
                        self._logger.info("Client disconnected (no data)")
                        break

                    # Parse command (support multiple commands separated by newline)
                    commands_data = data.decode("utf-8").strip().split("\n")

                    for command_str in commands_data:
                        if not command_str.strip():
                            continue

                        try:
                            command_data = json.loads(command_str)
                            command = command_data.get("command")
                            params = command_data.get("params", {})
                        except json.JSONDecodeError as e:
                            response = DaemonResponse(
                                success=False, error=f"Invalid JSON -> {e}"
                            )
                            await self._send_to_client(response)
                            continue

                        # Execute command
                        asyncio.create_task(self._process_and_respond(command, params))

                except asyncio.TimeoutError:
                    # Timeout is normal, just check running state and continue
                    continue
                except (
                    BrokenPipeError,
                    ConnectionResetError,
                    ConnectionAbortedError,
                ) as e:
                    self._logger.error(f"Client disconnected ({e})")
                    break
                except Exception as e:
                    self._logger.error(f"Error processing command -> {e}")
                    response = DaemonResponse(
                        success=False, error=f"Internal error -> {e}"
                    )
                    try:
                        await self._send_to_client(response)
                    except Exception:
                        break
                    await asyncio.sleep(0.5)

        except Exception as e:
            self._logger.error(f"Error handling client connection -> {e}")

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

    async def _process_and_respond(self, command: str, params: Dict[str, Any]):
        """Esegue il comando e invia la risposta in modo asincrono"""
        try:
            response = await self._execute_command(command, params)
            self._logger.info("Processed command, sending response", command=command)
            await self._send_to_client(response)
        except Exception as e:
            self._logger.error(f"Error in command processing -> {e}")
            error_response = DaemonResponse(
                success=False, error=f"Command processing failed: {str(e)}"
            )
            try:
                await self._send_to_client(error_response)
            except Exception:
                pass

    async def _send_to_client(self, response: DaemonResponse) -> bool:
        """
        Send data to the connected client.
        This method can be called from anywhere to push data to the client.

        Args:
            response: DaemonResponse to send

        Returns:
            True if sent successfully, False otherwise
        """
        async with self._client_connection_lock:
            if self._connected_client_writer is None:
                self._logger.warning("No client connected, cannot send data")
                return False

            try:
                self._connected_client_writer.write(response.to_json().encode("utf-8"))
                self._connected_client_writer.write(b"\n")  # Message delimiter
                await self._connected_client_writer.drain()
                return True
            except Exception as e:
                self._logger.error(f"Error sending data to client -> {e}")
                # Connection probably broken, clear it
                self._connected_client_reader = None
                self._connected_client_writer = None
                return False

    def is_client_connected(self) -> bool:
        """Check if a client is currently connected"""
        return self._connected_client_writer is not None

    async def broadcast_event(self, event_type: str, data: Any = None):
        """
        Broadcast an event to the connected client.
        This is useful for pushing notifications/updates without being requested.

        Args:
            event_type: Type of event (e.g., "server_started", "client_connected")
            data: Event data
        """
        if not self.is_client_connected():
            return

        event_response = DaemonResponse(
            success=True,
            data={
                "event": event_type,
                "event_data": data,
            },
        )
        await self._send_to_client(event_response)

    async def _execute_command(
        self, command: str, params: Dict[str, Any]
    ) -> DaemonResponse:
        """
        Execute a daemon command.

        Args:
            command: Command to execute
            params: Command parameters

        Returns:
            DaemonResponse with result
        """
        self._logger.debug(f"Executing command: {command} with params: {params}")

        # Check if command exists
        handler = self._command_handlers.get(command)
        if not handler:
            return DaemonResponse(success=False, error=f"Unknown command: {command}")

        # Execute handler
        try:
            return await handler(params)
        except Exception as e:
            self._logger.error(f"Error executing command {command} -> {e}")
            return DaemonResponse(
                success=False, error=f"Command execution failed: {str(e)}"
            )

    # ==================== Command Handlers: Service Control ====================

    async def _handle_start_server(self, params: Dict[str, Any]) -> DaemonResponse:
        """Start the server service"""
        if self._server and self._server.is_running():
            return DaemonResponse(success=False, error="Server already running")

        # Check if client is running (mutual exclusion)
        if self._client and self._client.is_running():
            return DaemonResponse(
                success=False, error="Cannot start server while client is running"
            )

        try:
            if not self._server:
                return DaemonResponse(success=False, error="Server not initialized")

            success = await self._server.start()
            if success:
                response_data = {
                    "message": "Server started successfully",
                    "host": self._server.config.host,
                    "port": self._server.config.port,
                    "enabled_streams": self._server.get_enabled_streams(),
                }
                self._logger.set_level(self._server.config.log_level)
                # Broadcast event to connected client
                # await self.broadcast_event("server_started", response_data)

                return DaemonResponse(success=True, data=response_data)
            else:
                return DaemonResponse(success=False, error="Failed to start server")
        except Exception as e:
            self._logger.error(f"Error starting server -> {e}")
            return DaemonResponse(
                success=False, error=f"Error starting server: {str(e)}"
            )

    async def _handle_stop_server(self, params: Dict[str, Any]) -> DaemonResponse:
        """Stop the server service"""
        if not self._server or not self._server.is_running():
            return DaemonResponse(success=False, error="Server not running")

        try:
            await self._server.stop()

            # Broadcast event to connected client
            # await self.broadcast_event("server_stopped", {"message": "Server stopped"})

            return DaemonResponse(
                success=True, data={"message": "Server stopped successfully"}
            )
        except Exception as e:
            return DaemonResponse(
                success=False, error=f"Error stopping server: {str(e)}"
            )

    async def _handle_start_client(self, params: Dict[str, Any]) -> DaemonResponse:
        """Start the client service"""
        if self._client and self._client.is_running():
            return DaemonResponse(success=False, error="Client already running")

        # Check if server is running (mutual exclusion)
        if self._server and self._server.is_running():
            return DaemonResponse(
                success=False, error="Cannot start client while server is running"
            )

        if not self._client:
            return DaemonResponse(success=False, error="Client not initialized")

        try:
            success = await self._client.start()
            if success:
                response_data = {
                    "message": "Client started successfully",
                    "server_host": self._client.config.get_server_host(),
                    "server_port": self._client.config.get_server_port(),
                    "enabled_streams": self._client.get_enabled_streams(),
                }
                self._logger.set_level(self._client.config.log_level)
                # Broadcast event to connected client
                # await self.broadcast_event("client_started", response_data)

                return DaemonResponse(success=True, data=response_data)
            else:
                return DaemonResponse(success=False, error="Failed to start client")
        except Exception as e:
            self._logger.error(f"Error starting client -> {e}")
            return DaemonResponse(
                success=False, error=f"Error starting client: {str(e)}"
            )

    async def _handle_stop_client(self, params: Dict[str, Any]) -> DaemonResponse:
        """Stop the client service"""
        if not self._client or not self._client.is_running():
            return DaemonResponse(success=False, error="Client not running")

        try:
            await self._client.stop()
            # self._client = None

            # Broadcast event to connected client
            # await self.broadcast_event("client_stopped", {"message": "Client stopped"})

            return DaemonResponse(
                success=True, data={"message": "Client stopped successfully"}
            )
        except Exception as e:
            return DaemonResponse(
                success=False, error=f"Error stopping client: {str(e)}"
            )

    # ==================== Command Handlers: Status ====================

    async def _handle_status(self, params: Dict[str, Any]) -> DaemonResponse:
        """Get overall daemon status"""
        server_running = self._server and self._server.is_running()
        client_running = self._client and self._client.is_running()

        status = {
            "daemon_running": self._running,
            "server_running": server_running,
            "client_running": client_running,
            "platform": "windows" if IS_WINDOWS else "unix",
            "socket_path": self.socket_path,
            "client_connected": self.is_client_connected(),
        }

        if server_running and self._server_config and self._server:
            connected_clients = self._server_config.get_clients()
            status["server_info"] = {  # type: ignore
                "host": self._server_config.host,
                "port": self._server_config.port,
                "connected_clients": len(connected_clients),
                "enabled_streams": self._server.get_enabled_streams(),
                "active_streams": self._server.get_active_streams(),
                "ssl_enabled": self._server_config.ssl_enabled,
            }

        if client_running and self._client_config and self._client:
            status["client_info"] = {  # type: ignore
                "server_host": self._client_config.get_server_host(),
                "server_port": self._client_config.get_server_port(),
                "connected": self._client.is_connected(),
                "enabled_streams": self._client.get_enabled_streams(),
                "active_streams": self._client.get_active_streams(),
                "ssl_enabled": self._client_config.ssl_enabled,
                "has_certificate": self._client.has_certificate(),
            }

        return DaemonResponse(success=True, data=status)

    async def _handle_server_status(self, params: Dict[str, Any]) -> DaemonResponse:
        """Get server status"""
        if not self._server:
            return DaemonResponse(success=True, data={"running": False})

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

        return DaemonResponse(success=True, data=status)

    async def _handle_client_status(self, params: Dict[str, Any]) -> DaemonResponse:
        """Get client status"""
        if not self._client:
            return DaemonResponse(success=True, data={"running": False})

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

        return DaemonResponse(success=True, data=status)

    # ==================== Command Handlers: Configuration ====================

    async def _handle_get_server_config(self, params: Dict[str, Any]) -> DaemonResponse:
        """Get server configuration"""
        if not self._server_config:
            return DaemonResponse(
                success=False, error="Server configuration not initialized"
            )

        config_dict = {
            "uid": self._server_config.uid,
            "host": self._server_config.host,
            "port": self._server_config.port,
            "heartbeat_interval": self._server_config.heartbeat_interval,
            "ssl_enabled": self._server_config.ssl_enabled,
            "log_level": self._server_config.log_level,
            "streams_enabled": self._server_config.streams_enabled,
        }
        return DaemonResponse(success=True, data=config_dict)

    async def _handle_set_server_config(self, params: Dict[str, Any]) -> DaemonResponse:
        """Set server configuration"""
        if self._server:
            return DaemonResponse(
                success=False,
                error="Cannot modify configuration while server is running",
            )

        if not self._server_config:
            return DaemonResponse(
                success=False, error="Server configuration not initialized"
            )

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

            return DaemonResponse(
                success=True, data={"message": "Server configuration updated"}
            )
        except Exception as e:
            return DaemonResponse(
                success=False, error=f"Error updating server configuration: {str(e)}"
            )

    async def _handle_get_client_config(self, params: Dict[str, Any]) -> DaemonResponse:
        """Get client configuration"""
        if not self._client_config:
            return DaemonResponse(
                success=False, error="Client configuration not initialized"
            )

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
        return DaemonResponse(success=True, data=config_dict)

    async def _handle_set_client_config(self, params: Dict[str, Any]) -> DaemonResponse:
        """Set client configuration"""
        if self._client:
            return DaemonResponse(
                success=False,
                error="Cannot modify configuration while client is running",
            )

        if not self._client_config:
            return DaemonResponse(
                success=False, error="Client configuration not initialized"
            )

        try:
            # Update configuration
            if "server_host" in params or "server_port" in params:
                host = params.get("server_host", self._client_config.get_server_host())
                port = params.get("server_port", self._client_config.get_server_port())
                auto_reconnect = params.get(
                    "auto_reconnect", self._client_config.do_auto_reconnect()
                )
                self._client_config.set_server_connection(
                    host=host, port=port, auto_reconnect=auto_reconnect
                )

            if "heartbeat_interval" in params:
                self._client_config.heartbeat_interval = int(  # ty:ignore[invalid-assignment]
                    params.get(
                        "heartbeat_interval",
                        self._client_config.get_heartbeat_interval(),
                    )
                )
            if "auto_reconnect" in params and "server_host" not in params:
                self._client_config.auto_reconnect = params.get(  # ty:ignore[invalid-assignment]
                    "auto_reconnect", self._client_config.do_auto_reconnect()
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
            if "hostname" in params:
                self._client_config.set_hostname(params["hostname"])
            if "uid" in params:
                self._client_config.uid = params.get("uid")

            return DaemonResponse(
                success=True, data={"message": "Client configuration updated"}
            )
        except Exception as e:
            return DaemonResponse(
                success=False, error=f"Error updating client configuration: {str(e)}"
            )

    async def _handle_save_config(self, params: Dict[str, Any]) -> DaemonResponse:
        """Save configurations to disk"""
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

            return DaemonResponse(
                success=True, data={"message": f"Configuration saved ({config_type})"}
            )
        except Exception as e:
            return DaemonResponse(
                success=False, error=f"Error saving configuration: {str(e)}"
            )

    async def _handle_reload_config(self, params: Dict[str, Any]) -> DaemonResponse:
        """Reload configurations from disk"""
        if self._server or self._client:
            return DaemonResponse(
                success=False,
                error="Cannot reload configuration while services are running",
            )

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

            return DaemonResponse(
                success=True,
                data={"message": f"Configuration reloaded ({config_type})"},
            )
        except Exception as e:
            return DaemonResponse(
                success=False, error=f"Error reloading configuration: {str(e)}"
            )

    # ==================== Command Handlers: Stream Management ====================

    async def _handle_enable_stream(self, params: Dict[str, Any]) -> DaemonResponse:
        """Enable a stream on running service"""
        stream_type = params.get("stream_type")
        service_type = params.get("service", "auto")  # "server", "client", or "auto"

        if stream_type is None:
            return DaemonResponse(
                success=False, error="Missing 'stream_type' parameter"
            )

        try:
            # Convert string to StreamType if needed
            if isinstance(stream_type, str):
                stream_type = int(stream_type)

            # Determine which service to use
            if service_type == "auto":
                if self._server:
                    service = self._server
                    service_name = "server"
                elif self._client:
                    service = self._client
                    service_name = "client"
                else:
                    return DaemonResponse(success=False, error="No service is running")
            elif service_type == "server":
                if not self._server:
                    return DaemonResponse(success=False, error="Server is not running")
                service = self._server
                service_name = "server"
            elif service_type == "client":
                if not self._client:
                    return DaemonResponse(success=False, error="Client is not running")
                service = self._client
                service_name = "client"
            else:
                return DaemonResponse(
                    success=False, error=f"Invalid service type: {service_type}"
                )

            # Enable stream
            await service.enable_stream_runtime(stream_type)

            return DaemonResponse(
                success=True,
                data={
                    "message": f"Stream {stream_type} enabled on {service_name}",
                    "active_streams": service.get_active_streams(),
                },
            )
        except Exception as e:
            return DaemonResponse(
                success=False, error=f"Error enabling stream: {str(e)}"
            )

    async def _handle_disable_stream(self, params: Dict[str, Any]) -> DaemonResponse:
        """Disable a stream on running service"""
        stream_type = params.get("stream_type")
        service_type = params.get("service", "auto")

        if stream_type is None:
            return DaemonResponse(
                success=False, error="Missing 'stream_type' parameter"
            )

        try:
            # Convert string to StreamType if needed
            if isinstance(stream_type, str):
                stream_type = int(stream_type)

            # Determine which service to use
            if service_type == "auto":
                if self._server:
                    service = self._server
                    service_name = "server"
                elif self._client:
                    service = self._client
                    service_name = "client"
                else:
                    return DaemonResponse(success=False, error="No service is running")
            elif service_type == "server":
                if not self._server:
                    return DaemonResponse(success=False, error="Server is not running")
                service = self._server
                service_name = "server"
            elif service_type == "client":
                if not self._client:
                    return DaemonResponse(success=False, error="Client is not running")
                service = self._client
                service_name = "client"
            else:
                return DaemonResponse(
                    success=False, error=f"Invalid service type: {service_type}"
                )

            # Disable stream
            await service.disable_stream_runtime(stream_type)

            return DaemonResponse(
                success=True,
                data={
                    "message": f"Stream {stream_type} disabled on {service_name}",
                    "active_streams": service.get_active_streams(),
                },
            )
        except Exception as e:
            return DaemonResponse(
                success=False, error=f"Error disabling stream: {str(e)}"
            )

    async def _handle_get_streams(self, params: Dict[str, Any]) -> DaemonResponse:
        """Get stream information"""
        service_type = params.get("service", "auto")

        # Determine which service to use
        if service_type == "auto":
            if self._server:
                service = self._server
                service_name = "server"
            elif self._client:
                service = self._client
                service_name = "client"
            else:
                return DaemonResponse(success=False, error="No service is running")
        elif service_type == "server":
            if not self._server:
                return DaemonResponse(success=False, error="Server is not running")
            service = self._server
            service_name = "server"
        elif service_type == "client":
            if not self._client:
                return DaemonResponse(success=False, error="Client is not running")
            service = self._client
            service_name = "client"
        else:
            return DaemonResponse(
                success=False, error=f"Invalid service type: {service_type}"
            )

        return DaemonResponse(
            success=True,
            data={
                "service": service_name,
                "enabled_streams": service.get_enabled_streams(),
                "active_streams": service.get_active_streams(),
            },
        )

    # ==================== Command Handlers: Client Management (Server) ====================

    async def _handle_add_client(self, params: Dict[str, Any]) -> DaemonResponse:
        """Add a client to server (server only)"""
        if not self._server:
            return DaemonResponse(success=False, error="Server is not running")

        try:
            hostname = params.get("hostname")
            ip_address = params.get("ip_address")
            screen_position = params.get("screen_position")

            if not hostname and not ip_address:
                return DaemonResponse(
                    success=False, error="Must provide either hostname or ip_address"
                )

            if not screen_position:
                return DaemonResponse(
                    success=False, error="Must provide screen_position"
                )

            await self._server.add_client(
                hostname=hostname,
                ip_address=ip_address,
                screen_position=screen_position,
            )

            return DaemonResponse(
                success=True,
                data={"message": f"Client added at position {screen_position}"},
            )
        except Exception as e:
            return DaemonResponse(success=False, error=f"Error adding client: {str(e)}")

    async def _handle_remove_client(self, params: Dict[str, Any]) -> DaemonResponse:
        """Remove a client from server (server only)"""
        if not self._server:
            return DaemonResponse(success=False, error="Server is not running")

        try:
            hostname = params.get("hostname")
            ip_address = params.get("ip_address")

            if not hostname and not ip_address:
                return DaemonResponse(
                    success=False, error="Must provide either hostname or ip_address"
                )

            await self._server.remove_client(hostname=hostname, ip_address=ip_address)

            return DaemonResponse(success=True, data={"message": "Client removed"})
        except Exception as e:
            return DaemonResponse(
                success=False, error=f"Error removing client: {str(e)}"
            )

    async def _handle_edit_client(self, params: Dict[str, Any]) -> DaemonResponse:
        """Edit a client configuration (server only)"""
        if not self._server or not self._server.is_running():
            return DaemonResponse(success=False, error="Server is not running")

        try:
            hostname = params.get("hostname")
            ip_address = params.get("ip_address")
            new_screen_position = params.get("new_screen_position")

            if not hostname and not ip_address:
                return DaemonResponse(
                    success=False, error="Must provide either hostname or ip_address"
                )

            if not new_screen_position:
                return DaemonResponse(
                    success=False, error="Must provide new_screen_position"
                )

            await self._server.edit_client(
                hostname=hostname,
                ip_address=ip_address,
                new_screen_position=new_screen_position,
            )

            return DaemonResponse(
                success=True,
                data={"message": f"Client updated to position {new_screen_position}"},
            )
        except Exception as e:
            return DaemonResponse(
                success=False, error=f"Error editing client: {str(e)}"
            )

    async def _handle_list_clients(self, params: Dict[str, Any]) -> DaemonResponse:
        """List registered clients (server only)"""
        if not self._server:
            return DaemonResponse(success=False, error="Server not initialized")

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

            return DaemonResponse(
                success=True, data={"count": len(clients_data), "clients": clients_data}
            )
        except Exception as e:
            return DaemonResponse(
                success=False, error=f"Error listing clients: {str(e)}"
            )

    # ==================== Command Handlers: SSL/Certificate ====================

    async def _handle_enable_ssl(self, params: Dict[str, Any]) -> DaemonResponse:
        """Enable SSL"""
        service_type = params.get("service", "auto")

        # Determine which service
        if service_type == "auto":
            if self._server:
                service = self._server
                config = self._server_config
                service_name = "server"
            elif self._client:
                service = self._client
                config = self._client_config
                service_name = "client"
            else:
                return DaemonResponse(success=False, error="No service initialized")
        elif service_type == "server":
            if not self._server:
                return DaemonResponse(success=False, error="Server not initialized")
            service = self._server
            config = self._server_config
            service_name = "server"
        elif service_type == "client":
            if not self._client:
                return DaemonResponse(success=False, error="Client not initialized")
            service = self._client
            config = self._client_config
            service_name = "client"
        else:
            return DaemonResponse(
                success=False, error=f"Invalid service type: {service_type}"
            )

        if not config:
            return DaemonResponse(
                success=False,
                error=f"{service_name.capitalize()} configuration not initialized",
            )

        try:
            if hasattr(service, "enable_ssl"):
                result = service.enable_ssl()
                if result:
                    config.enable_ssl()
                    return DaemonResponse(
                        success=True, data={"message": f"SSL enabled on {service_name}"}
                    )
                else:
                    return DaemonResponse(
                        success=False,
                        error="Failed to enable SSL or to load certificates",
                    )
            else:
                config.enable_ssl()
                return DaemonResponse(
                    success=True,
                    data={
                        "message": f"SSL enabled in {service_name} config (restart required)"
                    },
                )
        except Exception as e:
            return DaemonResponse(success=False, error=f"Error enabling SSL: {str(e)}")

    async def _handle_disable_ssl(self, params: Dict[str, Any]) -> DaemonResponse:
        """Disable SSL"""
        service_type = params.get("service", "auto")

        # Determine which service
        if service_type == "auto":
            if self._server:
                service = self._server
                config = self._server_config
                service_name = "server"
            elif self._client:
                service = self._client
                config = self._client_config
                service_name = "client"
            else:
                return DaemonResponse(success=False, error="No service initialized")
        elif service_type == "server":
            if not self._server:
                return DaemonResponse(success=False, error="Server not initialized")
            service = self._server
            config = self._server_config
            service_name = "server"
        elif service_type == "client":
            if not self._client:
                return DaemonResponse(success=False, error="Client not initialized")
            service = self._client
            config = self._client_config
            service_name = "client"
        else:
            return DaemonResponse(
                success=False, error=f"Invalid service type: {service_type}"
            )

        if not config:
            return DaemonResponse(
                success=False,
                error=f"{service_name.capitalize()} configuration not initialized",
            )

        try:
            if hasattr(service, "disable_ssl"):
                service.disable_ssl()
            config.disable_ssl()
            return DaemonResponse(
                success=True, data={"message": f"SSL disabled on {service_name}"}
            )
        except Exception as e:
            return DaemonResponse(success=False, error=f"Error disabling SSL: {str(e)}")

    async def _handle_share_certificate(self, params: Dict[str, Any]) -> DaemonResponse:
        """Share certificate (server only)"""
        if not self._server or not self._server.is_running():
            return DaemonResponse(success=False, error="Server is not running")

        try:
            host = params.get("host", self._server.config.host)
            timeout = params.get("timeout", 30)
            otp = await self._server.share_certificate(host=host, timeout=timeout)

            return DaemonResponse(
                success=True,
                data={
                    "message": "Certificate sharing started",
                    "otp": otp,
                    "timeout": timeout,
                    "instructions": "Provide this OTP to clients to receive the certificate",
                },
            )
        except Exception as e:
            return DaemonResponse(
                success=False, error=f"Error sharing certificate: {str(e)}"
            )

    async def _handle_receive_certificate(
        self, params: Dict[str, Any]
    ) -> DaemonResponse:
        """Receive certificate (client only)"""
        if not self._client:
            return DaemonResponse(success=False, error="Client not initialized")

        try:
            otp = params.get("otp")
            if not otp:
                return DaemonResponse(
                    success=False, error="Must provide 'otp' parameter"
                )

            success = await self._client.set_otp(otp)

            if success:
                return DaemonResponse(
                    success=True,
                    data={
                        "message": "Certificate received successfully",
                        "certificate_path": self._client.get_certificate_path(),
                    },
                )
            else:
                return DaemonResponse(
                    success=False,
                    error="Failed to receive certificate (invalid OTP or network error)",
                )
        except Exception as e:
            return DaemonResponse(
                success=False, error=f"Error receiving certificate: {str(e)}"
            )

    # ==================== Command Handlers: Server Selection & OTP ====================

    async def _handle_check_server_choice_needed(
        self, params: Dict[str, Any]
    ) -> DaemonResponse:
        """Check if server choice is needed (client only)"""
        if not self._client:
            return DaemonResponse(success=False, error="Client not initialized")

        try:
            # Check if we're currently waiting for server choice
            needed = await self._client.server_choice_needed()

            return DaemonResponse(
                success=True,
                data={
                    "server_choice_needed": needed,
                    "message": "Please choose a server from the found servers"
                    if needed
                    else "No server choice needed",
                },
            )
        except Exception as e:
            return DaemonResponse(
                success=False, error=f"Error checking server choice: {str(e)}"
            )

    async def _handle_get_found_servers(self, params: Dict[str, Any]) -> DaemonResponse:
        """Get list of found servers (client only)"""
        if not self._client:
            return DaemonResponse(success=False, error="Client not initialized")

        try:
            servers = self._client.get_found_servers()

            # Convert services to dict format
            servers_data = []
            for s in servers:
                servers_data.append(
                    {
                        "name": s.name,
                        "address": s.address,
                        "port": s.port,
                        "hostname": s.hostname,
                        "uid": s.uid,
                    }
                )

            return DaemonResponse(
                success=True, data={"servers": servers_data, "count": len(servers_data)}
            )
        except Exception as e:
            return DaemonResponse(
                success=False, error=f"Error getting found servers: {str(e)}"
            )

    async def _handle_choose_server(self, params: Dict[str, Any]) -> DaemonResponse:
        """Choose a server from found servers (client only)"""
        if not self._client:
            return DaemonResponse(success=False, error="Client not initialized")

        try:
            uid = params.get("uid")
            if not uid:
                return DaemonResponse(
                    success=False, error="Must provide 'uid' parameter"
                )

            # Choose the server
            self._client.choose_server(uid)

            return DaemonResponse(
                success=True,
                data={
                    "message": f"Server {uid} selected",
                    "server_host": self._client.config.get_server_host(),
                    "server_port": self._client.config.get_server_port(),
                },
            )
        except Exception as e:
            return DaemonResponse(
                success=False, error=f"Error choosing server: {str(e)}"
            )

    async def _handle_check_otp_needed(self, params: Dict[str, Any]) -> DaemonResponse:
        """Check if OTP is needed for certificate (client only)"""
        if not self._client:
            return DaemonResponse(success=False, error="Client not initialized")

        try:
            # Check if we're currently waiting for OTP
            needed = await self._client.otp_needed()

            return DaemonResponse(
                success=True,
                data={
                    "otp_needed": needed,
                    "message": "Please provide OTP from server"
                    if needed
                    else "No OTP needed",
                },
            )
        except Exception as e:
            return DaemonResponse(
                success=False, error=f"Error checking OTP status: {str(e)}"
            )

    async def _handle_set_otp(self, params: Dict[str, Any]) -> DaemonResponse:
        """Set OTP for certificate reception (client only)"""
        if not self._client:
            return DaemonResponse(success=False, error="Client not initialized")

        try:
            otp = params.get("otp")
            if not otp:
                return DaemonResponse(
                    success=False, error="Must provide 'otp' parameter"
                )

            success = await self._client.set_otp(otp)

            if success:
                return DaemonResponse(
                    success=True,
                    data={
                        "message": "OTP set successfully",
                        "certificate_path": self._client.get_certificate_path()
                        if self._client.has_certificate()
                        else None,
                    },
                )
            else:
                return DaemonResponse(
                    success=False,
                    error="Failed to set OTP (invalid format or already set)",
                )
        except Exception as e:
            return DaemonResponse(success=False, error=f"Error setting OTP: {str(e)}")

    # ==================== Command Handlers: Service Discovery ====================

    async def _get_discovered_services(self, params: Dict[str, Any]) -> DaemonResponse:
        """Get available services on network"""
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

                return DaemonResponse(
                    success=True,
                    data={"services": services_data, "count": len(services_data)},
                )
            else:
                return DaemonResponse(
                    success=False,
                    error="Client not initialized; cannot perform service discovery",
                )
        except Exception as e:
            return DaemonResponse(
                success=False, error=f"Error discovering services: {str(e)}"
            )

    # ==================== Command Handlers: Daemon Control ====================

    async def _handle_shutdown(self, params: Dict[str, Any]) -> DaemonResponse:
        """Shutdown the daemon"""
        # Schedule shutdown after response is sent
        asyncio.create_task(self._delayed_shutdown())
        return DaemonResponse(success=True, data={"message": "Daemon shutting down..."})

    async def _delayed_shutdown(self):
        """Delay shutdown to allow response to be sent"""
        await asyncio.sleep(0.5)
        await self.stop()

    async def _handle_ping(self, params: Dict[str, Any]) -> DaemonResponse:
        """Simple ping command to check daemon is alive"""
        return DaemonResponse(success=True, data={"message": "pong"})

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

    command_data = {"command": command, "params": params or {}}

    if IS_WINDOWS or ":" in socket_path:
        # Windows TCP socket or explicit TCP address
        return await _send_tcp_command(socket_path, command_data, timeout)
    else:
        # Unix socket
        return await _send_unix_command(socket_path, command_data, timeout)


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
        writer.write(json.dumps(command_data).encode("utf-8"))
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
        writer.write(json.dumps(command_data).encode("utf-8"))
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

    args = parser.parse_args()

    # Setup application config
    app_config = ApplicationConfig()
    if args.config_dir:
        app_config.set_save_path(args.config_dir)
    app_config.config_path = "_test_config/"

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
    # Use uvloop for better performance if available
    try:
        if IS_WINDOWS:
            import winloop as asyncloop  # ty:ignore[unresolved-import]
        else:
            import uvloop as asyncloop  # ty:ignore[unresolved-import]

        asyncloop.run(main())
    except ImportError:
        asyncio.run(main())
