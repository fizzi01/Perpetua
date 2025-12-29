"""
Daemon service for managing lifecycle.

This module provides a daemon service that can run independently from a GUI,
managing both Client and Server services through a command socket interface.
The daemon exposes a Unix socket (Linux/macOS) or Named Pipe (Windows) for
receiving commands to control the application.
"""

import asyncio
import json
import os
import signal
import sys
from typing import Optional, Dict, Any, Callable
from enum import Enum

from config import ApplicationConfig, ServerConfig, ClientConfig
from service.client import Client
from service.server import Server
from utils.logging import Logger, get_logger

# Determine platform for socket type
IS_WINDOWS = sys.platform in ("win32", "cygwin", "cli")

if IS_WINDOWS:
    try:
        import win32pipe
        import win32file
        import pywintypes
    except ImportError:
        # Windows without pywin32
        IS_WINDOWS = False


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
    Supports both Unix sockets (Linux/macOS) and Named Pipes (Windows).

    Example:
        # Create and start daemon
        daemon = PyContinuityDaemon(
            socket_path="/tmp/pycontinuity.sock",  # or r"\\.\\pipe\\pycontinuity" on Windows
            app_config=ApplicationConfig()
        )
        await daemon.start()

        # Daemon will run until shutdown command is received
        await daemon.wait_for_shutdown()

        # Cleanup
        await daemon.stop()
    """

    # Platform-specific default paths
    if IS_WINDOWS:
        DEFAULT_SOCKET_PATH = r"\\.\\pipe\\pycontinuity_daemon"
    else:
        DEFAULT_SOCKET_PATH = "/tmp/pycontinuity_daemon.sock"

    MAX_CONNECTIONS = 10
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
            socket_path: Path to Unix socket or Named Pipe for command interface
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
            DaemonCommand.DISCOVER_SERVICES: self._handle_discover_services,
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

    async def start(self) -> bool:
        """
        Start the daemon and command socket server.

        Returns:
            True if daemon started successfully, False otherwise
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
                self._logger.warning(f"Could not load configurations: {e}")

        # Create socket server based on platform
        try:
            if IS_WINDOWS:
                # Windows Named Pipe (requires custom implementation)
                await self._start_windows_server()
            else:
                # Unix socket
                await self._start_unix_server()

            self._running = True
            self._logger.info(f"Daemon started, listening on {self.socket_path}")
            return True

        except Exception as e:
            self._logger.error(f"Failed to start daemon: {e}")
            return False

    async def _start_unix_server(self):
        """Start Unix socket server (Linux/macOS)"""
        # Remove existing socket if present
        if os.path.exists(self.socket_path):
            os.unlink(self.socket_path)

        self._socket_server = await asyncio.start_unix_server(
            self._handle_client_connection, path=self.socket_path
        )

        # Set socket permissions (owner read/write only)
        os.chmod(self.socket_path, 0o600)

    async def _start_windows_server(self):
        """Start Named Pipe server (Windows)"""
        # For Windows, we'll use asyncio streams with a custom pipe handler
        # This is a simplified version - production should use more robust implementation
        asyncio.create_task(self._windows_pipe_server())

    async def _windows_pipe_server(self):
        """Windows named pipe server loop"""
        while self._running:
            try:
                # Create named pipe
                pipe = win32pipe.CreateNamedPipe(
                    self.socket_path,
                    win32pipe.PIPE_ACCESS_DUPLEX,
                    win32pipe.PIPE_TYPE_MESSAGE
                    | win32pipe.PIPE_READMODE_MESSAGE
                    | win32pipe.PIPE_WAIT,
                    self.MAX_CONNECTIONS,
                    self.BUFFER_SIZE,
                    self.BUFFER_SIZE,
                    0,
                    None,
                )

                # Wait for client connection
                win32pipe.ConnectNamedPipe(pipe, None)

                # Handle connection in background
                asyncio.create_task(self._handle_windows_pipe(pipe))

            except Exception as e:
                self._logger.error(f"Windows pipe server error: {e}")
                await asyncio.sleep(0.1)

    async def _handle_windows_pipe(self, pipe):
        """Handle Windows named pipe connection"""
        try:
            # Read command
            result, data = win32file.ReadFile(pipe, self.BUFFER_SIZE)
            if result == 0 and data:
                # Parse and execute command
                try:
                    command_data = json.loads(data.decode("utf-8"))
                    command = command_data.get("command")
                    params = command_data.get("params", {})

                    response = await self._execute_command(command, params)

                    # Send response
                    win32file.WriteFile(pipe, response.to_json().encode("utf-8"))

                except json.JSONDecodeError as e:
                    response = DaemonResponse(success=False, error=f"Invalid JSON: {e}")
                    win32file.WriteFile(pipe, response.to_json().encode("utf-8"))

        except Exception as e:
            self._logger.error(f"Error handling Windows pipe: {e}")
        finally:
            win32file.CloseHandle(pipe)

    async def stop(self):
        """Stop the daemon and cleanup resources"""
        if not self._running:
            self._logger.warning("Daemon not running")
            return

        self._logger.info("Stopping daemon...")

        self._running = False

        # Stop services
        if self._server:
            await self._server.stop()
            self._server = None

        if self._client:
            await self._client.stop()
            self._client = None

        # Close socket server
        if self._socket_server and not IS_WINDOWS:
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
        Handle incoming client connection on command socket (Unix).

        Args:
            reader: Stream reader for receiving data
            writer: Stream writer for sending responses
        """
        addr = writer.get_extra_info("peername")
        self._logger.debug(f"New connection from {addr}")

        try:
            # Read command from client
            data = await reader.read(self.BUFFER_SIZE)
            if not data:
                return

            # Parse command
            try:
                command_data = json.loads(data.decode("utf-8"))
                command = command_data.get("command")
                params = command_data.get("params", {})
            except json.JSONDecodeError as e:
                response = DaemonResponse(success=False, error=f"Invalid JSON: {e}")
                writer.write(response.to_json().encode("utf-8"))
                await writer.drain()
                return

            # Execute command
            response = await self._execute_command(command, params)

            # Send response
            writer.write(response.to_json().encode("utf-8"))
            await writer.drain()

        except Exception as e:
            self._logger.error(f"Error handling client connection: {e}")
            response = DaemonResponse(success=False, error=f"Internal error: {e}")
            try:
                writer.write(response.to_json().encode("utf-8"))
                await writer.drain()
            except:
                pass

        finally:
            writer.close()
            await writer.wait_closed()

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
            self._logger.error(f"Error executing command {command}: {e}", exc_info=True)
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
            self._server = Server(
                app_config=self.app_config,
                server_config=self._server_config,
                auto_load_config=False,  # Already loaded
            )

            success = await self._server.start()
            if success:
                return DaemonResponse(
                    success=True,
                    data={
                        "message": "Server started successfully",
                        "host": self._server.config.host,
                        "port": self._server.config.port,
                        "enabled_streams": self._server.get_enabled_streams(),
                    },
                )
            else:
                return DaemonResponse(success=False, error="Failed to start server")
        except Exception as e:
            self._logger.error(f"Error starting server: {e}", exc_info=True)
            return DaemonResponse(
                success=False, error=f"Error starting server: {str(e)}"
            )

    async def _handle_stop_server(self, params: Dict[str, Any]) -> DaemonResponse:
        """Stop the server service"""
        if not self._server or not self._server.is_running():
            return DaemonResponse(success=False, error="Server not running")

        try:
            await self._server.stop()
            self._server = None
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

        try:
            self._client = Client(
                app_config=self.app_config,
                client_config=self._client_config,
                auto_load_config=False,  # Already loaded
            )

            success = await self._client.start()
            if success:
                return DaemonResponse(
                    success=True,
                    data={
                        "message": "Client started successfully",
                        "server_host": self._client.config.get_server_host(),
                        "server_port": self._client.config.get_server_port(),
                        "enabled_streams": self._client.get_enabled_streams(),
                    },
                )
            else:
                return DaemonResponse(success=False, error="Failed to start client")
        except Exception as e:
            self._logger.error(f"Error starting client: {e}", exc_info=True)
            return DaemonResponse(
                success=False, error=f"Error starting client: {str(e)}"
            )

    async def _handle_stop_client(self, params: Dict[str, Any]) -> DaemonResponse:
        """Stop the client service"""
        if not self._client or not self._client.is_running():
            return DaemonResponse(success=False, error="Client not running")

        try:
            await self._client.stop()
            self._client = None
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
        }

        if server_running:
            connected_clients = self._server.clients_manager.get_clients()
            status["server_info"] = {  # type: ignore
                "host": self._server.config.host,
                "port": self._server.config.port,
                "connected_clients": len(connected_clients),
                "enabled_streams": self._server.get_enabled_streams(),
                "active_streams": self._server.get_active_streams(),
                "ssl_enabled": self._server.config.ssl_enabled,
            }

        if client_running:
            status["client_info"] = {  # type: ignore
                "server_host": self._client.config.get_server_host(),
                "server_port": self._client.config.get_server_port(),
                "connected": self._client.is_connected(),
                "enabled_streams": self._client.get_enabled_streams(),
                "active_streams": self._client.get_active_streams(),
                "ssl_enabled": self._client.config.ssl_enabled,
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
        config_dict = {
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
        if self._server and self._server.is_running():
            return DaemonResponse(
                success=False,
                error="Cannot modify configuration while server is running",
            )

        try:
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
        config_dict = {
            "server_host": self._client_config.get_server_host(),
            "server_port": self._client_config.get_server_port(),
            "heartbeat_interval": self._client_config.get_heartbeat_interval(),
            "auto_reconnect": self._client_config.do_auto_reconnect(),
            "ssl_enabled": self._client_config.ssl_enabled,
            "log_level": self._client_config.log_level,
            "streams_enabled": self._client_config.streams_enabled,
            "hostname": self._client_config.get_hostname(),
        }
        return DaemonResponse(success=True, data=config_dict)

    async def _handle_set_client_config(self, params: Dict[str, Any]) -> DaemonResponse:
        """Set client configuration"""
        if self._client and self._client.is_running():
            return DaemonResponse(
                success=False,
                error="Cannot modify configuration while client is running",
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
                self._client_config.heartbeat_interval = params["heartbeat_interval"]
            if "auto_reconnect" in params and "server_host" not in params:
                self._client_config.auto_reconnect = params["auto_reconnect"]
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
                await self._server_config.save()

            if config_type in ("client", "both"):
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
        if (self._server and self._server.is_running()) or (
            self._client and self._client.is_running()
        ):
            return DaemonResponse(
                success=False,
                error="Cannot reload configuration while services are running",
            )

        try:
            config_type = params.get("type", "both")

            if config_type in ("server", "both"):
                await self._server_config.load()

            if config_type in ("client", "both"):
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
                if self._server and self._server.is_running():
                    service = self._server
                    service_name = "server"
                elif self._client and self._client.is_running():
                    service = self._client
                    service_name = "client"
                else:
                    return DaemonResponse(success=False, error="No service is running")
            elif service_type == "server":
                if not self._server or not self._server.is_running():
                    return DaemonResponse(success=False, error="Server is not running")
                service = self._server
                service_name = "server"
            elif service_type == "client":
                if not self._client or not self._client.is_running():
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
                if self._server and self._server.is_running():
                    service = self._server
                    service_name = "server"
                elif self._client and self._client.is_running():
                    service = self._client
                    service_name = "client"
                else:
                    return DaemonResponse(success=False, error="No service is running")
            elif service_type == "server":
                if not self._server or not self._server.is_running():
                    return DaemonResponse(success=False, error="Server is not running")
                service = self._server
                service_name = "server"
            elif service_type == "client":
                if not self._client or not self._client.is_running():
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
            if self._server and self._server.is_running():
                service = self._server
                service_name = "server"
            elif self._client and self._client.is_running():
                service = self._client
                service_name = "client"
            else:
                return DaemonResponse(success=False, error="No service is running")
        elif service_type == "server":
            if not self._server or not self._server.is_running():
                return DaemonResponse(success=False, error="Server is not running")
            service = self._server
            service_name = "server"
        elif service_type == "client":
            if not self._client or not self._client.is_running():
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
        if not self._server or not self._server.is_running():
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
        if not self._server or not self._server.is_running():
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
                        error="Failed to enable SSL (certificate may be missing)",
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
            otp = await self._server.share_certificate(host=host)

            return DaemonResponse(
                success=True,
                data={
                    "message": "Certificate sharing started",
                    "otp": otp,
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

    async def _handle_discover_services(self, params: Dict[str, Any]) -> DaemonResponse:
        """Discover available services on network"""
        try:
            timeout = params.get("timeout", 5)

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
        socket_path: Path to daemon socket or named pipe
        timeout: Command timeout in seconds

    Returns:
        Response dictionary

    Raises:
        ConnectionError: If cannot connect to daemon
        TimeoutError: If command times out
    """
    socket_path = socket_path or Daemon.DEFAULT_SOCKET_PATH

    command_data = {"command": command, "params": params or {}}

    if IS_WINDOWS:
        # Windows Named Pipe
        return await _send_windows_command(socket_path, command_data, timeout)
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


async def _send_windows_command(
    pipe_path: str, command_data: dict, timeout: float
) -> dict:
    """Send command via Windows Named Pipe"""
    try:
        # Open named pipe
        handle = win32file.CreateFile(
            pipe_path,
            win32file.GENERIC_READ | win32file.GENERIC_WRITE,
            0,
            None,
            win32file.OPEN_EXISTING,
            0,
            None,
        )

        try:
            # Send command
            win32file.WriteFile(handle, json.dumps(command_data).encode("utf-8"))

            # Read response
            result, data = win32file.ReadFile(handle, Daemon.BUFFER_SIZE)

            if result == 0 and data:
                response = json.loads(data.decode("utf-8"))
                return response
            else:
                raise ConnectionError("No response from daemon")

        finally:
            win32file.CloseHandle(handle)

    except pywintypes.error as e:
        raise ConnectionError(f"Cannot connect to daemon: {e}")


# ==================== Main Entry Point ====================


async def main():
    """Main entry point for daemon"""
    import argparse

    parser = argparse.ArgumentParser(description="Daemon")
    parser.add_argument(
        "--socket",
        default=Daemon.DEFAULT_SOCKET_PATH,
        help="Socket path (Unix socket or Windows Named Pipe)",
    )
    parser.add_argument("--config-dir", help="Configuration directory path")

    args = parser.parse_args()

    # Setup application config
    app_config = ApplicationConfig()
    if args.config_dir:
        app_config.set_save_path(args.config_dir)

    # Create and start daemon
    daemon = Daemon(
        socket_path=args.socket, app_config=app_config, auto_load_config=True
    )

    if not await daemon.start():
        print("Failed to start daemon")
        return 1

    print(f"Daemon started successfully on {daemon.get_socket_path()}")
    print(f"Platform: {'Windows (Named Pipe)' if IS_WINDOWS else 'Unix (Socket)'}")

    # Wait for shutdown
    try:
        await daemon.wait_for_shutdown()
    except KeyboardInterrupt:
        print("\nKeyboard interrupt received")
    finally:
        await daemon.stop()

    return 0


if __name__ == "__main__":
    # Use appropriate event loop for platform
    if IS_WINDOWS:
        try:
            import winloop

            winloop.run(main())
        except ImportError:
            asyncio.run(main())
    else:
        try:
            import uvloop

            uvloop.run(main())
        except ImportError:
            asyncio.run(main())
