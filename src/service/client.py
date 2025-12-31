"""
Unified Client API
Provides a clean interface to configure and manage client components.
"""

import asyncio
import os
from pathlib import Path
from typing import Optional, Dict

from config import ApplicationConfig, ClientConfig
from model.client import ClientObj, ClientsManager
from event.bus import AsyncEventBus
from event import EventType, ClientStreamReconnectedEvent
from network.connection.client import ConnectionHandler
from network.stream.handler.client import (
    UnidirectionalStreamHandler,
    BidirectionalStreamHandler,
)
from network.stream import StreamType
from network.stream.handler import StreamHandler
from command import CommandHandler
from input.mouse import ClientMouseController
from input.keyboard import ClientKeyboardController
from input.clipboard import ClipboardListener, ClipboardController
from service import ServiceDiscovery, Service
from utils.crypto import CertificateManager
from utils.crypto.sharing import CertificateReceiver
from utils.metrics import MetricsCollector, PerformanceMonitor
from utils.screen import Screen
from utils.logging import get_logger


class Client:
    """
    Manages client configurations, connections, and event-driven communication for various applications.

    This class is designed to handle the overall management of a client application in aspects such as
    interaction with other clients, secure communication using certificates, and managing event streams
    during runtime.

    Example:
    ::
        # Create client with unified configuration
        # Option 1: Use default config and configure programmatically
        client = Client()

        # Configure client
        client.config.set_server_connection(
            host="192.168.1.74", port=5555, auto_reconnect=True
        )
        client.config.set_logging(level=Logger.INFO)

        # Enable streams
        await client.enable_stream(StreamType.MOUSE)
        await client.enable_stream(StreamType.KEYBOARD)
        await client.enable_stream(StreamType.CLIPBOARD)

        # Save configuration for next time
        await client.save_config()

        # Option 2: Load existing configuration
        # client = Client(auto_load_config=True)

        # Start client
        if not await client.start():
            print("Failed to start client")
            return

        print("Client started successfully")
        print(
            f"Connecting to {client.config.get_server_host()}:{client.config.get_server_port()}"
        )
        print(f"Enabled streams: {client.get_enabled_streams()}")

        try:
            # Wait for connection
            await asyncio.sleep(5)

            if client.is_connected():
                print(f"Connected! Active streams: {client.get_active_streams()}")

            # Keep running
            while True:
                await asyncio.sleep(1)

        except KeyboardInterrupt:
            print("Keyboard interrupt received")
        finally:
            print("Stopping client...")
            await client.stop()
            # Save configuration on exit
            await client.save_config()
    """

    CLEANUP_DELAY = 0.5  # seconds to wait during cleanup

    def __init__(
        self,
        app_config: Optional[ApplicationConfig] = None,
        client_config: Optional[ClientConfig] = None,
        auto_load_config: bool = True,
    ):
        """
        Initializes an instance of the class by setting up configurations, core components, and registries
        necessary for managing clients, event handling, and connections.

        Args:
            app_config: The application configuration settings. Defaults to None if not provided.
            client_config: The client configuration settings including connection, SSL, logging, and streams.
                Defaults to None if not provided.
            auto_load_config: If True, automatically loads configuration from file if exists. Defaults to True.

        Attributes:
            _logger (Logger): Logger instance for the client.
            app_config (ApplicationConfig): Holds the application configuration details.
            config (ClientConfig): Holds all client settings including connection, SSL, logging, and streams.
            _cert_manager (CertificateManager): Manages certificate-related operations including loading
                certificates from directories.
            _cert_receiver (Optional[CertificateReceiver]): Represents the entity responsible for handling
                certificate reception. Default is None.
            clients_manager (ClientsManager): Manages the collection of clients in client mode.
            event_bus (AsyncEventBus): Handles asynchronous event-based communication.
            main_client (ClientObj): Represents the main client object with a placeholder IP address and
                hostname derived from the configuration.
            _stream_handlers (Dict[int, StreamHandler]): A registry mapping identifiers to stream handlers.
            _components (dict): Holds the registered components initialized for the application.
            _running (bool): Indicates whether the main processing is running. Default value is False.
            _connected (bool): Reflects the connection state of the client. Default value is False.
            connection_handler (Optional[ConnectionHandler]): Responsible for handling the connection with
                the external server. Default is None.
        """
        # Initialize configurations
        self.app_config = app_config or ApplicationConfig()
        self.config = client_config or ClientConfig(self.app_config)

        # Try to load existing configuration if requested
        if auto_load_config:
            self.config.sync_load()

        # Set logging level
        self._logger = get_logger(self.__class__.__name__, level=self.config.log_level)

        # Initialize certificate manager
        self._cert_manager = CertificateManager(
            cert_dir=self.app_config.get_certificate_path()
        )
        self._cert_receiver: Optional[CertificateReceiver] = None

        # Load certificate if available and SSL is enabled
        if self.config.ssl_enabled:
            if self._cert_manager.certificate_exist(
                source_id=self.config.get_server_host()
            ):
                self._load_certificate()

        # Initialize core components
        self.clients_manager = ClientsManager(client_mode=True)
        self.event_bus = AsyncEventBus()

        # Add main client to clients manager
        self.main_client = ClientObj(
            uid=self.config.get_uid(),
            ip_address="0.0.0.0",  # Dummy, we don't need it
            hostname=self.config.get_hostname(),
            screen_resolution=Screen.get_size_str(),
        )
        self.clients_manager.add_client(self.main_client)

        # Stream handlers registry
        self._stream_handlers: Dict[int, StreamHandler] = {}

        # Components registry
        self._components = {}
        self._running = False
        self._connected = False

        # Metrics
        self._metrics_collector = MetricsCollector()
        self._performance_monitor = PerformanceMonitor(self._metrics_collector)

        # mDNS Service Discovery
        self._mdns_service = ServiceDiscovery()
        self._found_services: list[Service] = []

        # Connection handler
        self.connection_handler: Optional[ConnectionHandler] = None

        # -- Inner config state --
        self._state_lock = asyncio.Lock()
        # If ssl is enabled but no cert, we will wait for it in connection, so we need to set the otp future
        self._otp_needed: asyncio.Future[bool] = asyncio.Future()
        self._otp_received: asyncio.Future[str] = asyncio.Future()

        self._need_server_choice: asyncio.Future[bool] = asyncio.Future()
        self._server: asyncio.Future[Service] = (
            asyncio.Future()
        )  # We will set the result when user choose a server

    # ==================== Configuration Management ====================

    async def save_config(self) -> bool:
        """
        Save current configuration to file.

        Returns:
            True if saved successfully, False otherwise
        """
        try:
            await self.config.save()
            self._logger.info("Configuration saved successfully")
            return True
        except Exception as e:
            self._logger.error(f"Error saving configuration: {e}")
            return False

    async def load_config(self) -> bool:
        """
        Load configuration from file.

        Returns:
            True if loaded successfully, False otherwise
        """
        try:
            if await self.config.load():
                self._logger.set_level(self.config.log_level)
                self._logger.info("Configuration loaded successfully")
                return True
            else:
                self._logger.warning("Configuration file not found")
                return False
        except Exception as e:
            self._logger.error(f"Error loading configuration: {e}")
            return False

    # ==================== Certificate Management ====================

    def enable_ssl(self) -> bool:
        """Enable SSL connection if certificate is loaded"""
        self.config.enable_ssl()
        if self.has_certificate():
            self._logger.info("SSL connection enabled")
            return True
        elif self._load_certificate():
            self._logger.info("SSL connection enabled")
            return True
        else:
            self._logger.warning("SSL Enabled but no valid certificate loaded")
            return False

    def disable_ssl(self) -> None:
        """Disable SSL connection"""
        self.config.disable_ssl()
        self._logger.info("SSL connection disabled")

    def has_certificate(self) -> bool:
        """Check if certificate exists for the server"""
        return self._cert_manager.certificate_exist(
            source_id=self.config.get_server_host()
        )

    def _load_certificate(self) -> Optional[str]:
        """
        Load SSL certificate from CertificateManager

        Returns:
            Path to loaded certificate or None if not found
        """
        server_uid = self.config.get_server_uid()
        if self._cert_manager.certificate_exist(source_id=server_uid):
            cert_path = self._cert_manager.get_ca_cert_path(source_id=server_uid)
            self._logger.info(f"Certificate loaded from {cert_path}")
            return cert_path
        else:
            self._logger.warning(f"Certificate not found for server {server_uid}")
            return None

    def _remove_certificate(self) -> None:
        """Remove loaded SSL certificate. It will disable SSL connection."""
        if self.has_certificate():
            self._logger.info("Removing certificate and disabling SSL connection")
            # Certificate removal would be handled by CertificateManager
            self.disable_ssl()
        else:
            self._logger.info("No certificate to remove")

    async def otp_needed(self) -> bool:
        """
        Check if OTP is needed for certificate receiving process.

        Returns:
            True if OTP is needed, False otherwise
        """
        async with self._state_lock:
            return await self._otp_needed

    async def set_otp(self, otp: str) -> bool:
        """
        Set the OTP for certificate receiving process.

        Args:
            otp: One-time password provided by server

        Returns:
            True if OTP is valid and set, False otherwise
        """

        if not otp or len(otp) != 6 or not otp.isdigit():
            self._logger.error("Invalid OTP format. Must be 6 digits")
            return False

        if not self._otp_received.done():
            self._otp_received.set_result(otp)
            # Give the event loop a chance to process the result
            await asyncio.sleep(0)
            self._logger.info("OTP set successfully")
            return True
        else:
            self._logger.warning("OTP has already been set")
            return False

    async def receive_certificate(
        self,
        otp: str,
        server_host: Optional[str] = None,
        server_port: int = ClientConfig.DEFAULT_SERVER_PORT - 2,
        timeout: int = 30,
    ) -> bool:
        """
        Receive CA certificate from server using OTP.

        Args:
            otp: One-time password provided by server
            server_host: Server host for certificate sharing (default: same as connection host)
            server_port: Server port for certificate sharing (default: 55556)
            timeout: Connection timeout in seconds (default: 30)

        Returns:
            True if certificate received and saved successfully

        Example:
        ::
            # Get OTP from server (out-of-band, e.g., displayed on server screen)
            otp = input("Enter OTP from server: ")
            success = await client.receive_certificate(otp)
            if success:
                print("Certificate received successfully!")
        """
        if not otp or len(otp) != 6 or not otp.isdigit():
            self._logger.error("Invalid OTP format. Must be 6 digits")
            return False

        # Use connection host if not specified
        if server_host is None:
            server_host = self.config.get_server_host()

        self._logger.info(
            f"Attempting to receive certificate from {server_host}:{server_port}"
        )

        try:
            # Create receiver
            self._cert_receiver = CertificateReceiver(
                server_host=server_host, server_port=server_port, timeout=timeout
            )

            # Receive certificate
            success, cert_data = await self._cert_receiver.receive_certificate(otp)

            if not success:
                self._logger.error("Failed to receive certificate from server")
                return False

            if not cert_data:
                self._logger.error("Received empty certificate data")
                return False

            # Save certificate
            if not self._cert_manager.save_ca_data(
                data=cert_data, source_id=self.config.get_server_uid()
            ):
                self._logger.error("Failed to save received certificate")
                return False

            # Load and enable SSL
            cur_path = self._load_certificate()
            if cur_path:
                cur_name = os.path.basename(cur_path)
                self._cert_manager.extend_mapping(
                    source_id=self._cert_receiver.get_resolved_host(),
                    cert_filename=cur_name,
                )
            self.config.enable_ssl()

            self._logger.info("Certificate received and saved successfully")
            return True

        except Exception as e:
            self._logger.error(f"Error receiving certificate -> {e}")
            import traceback

            self._logger.error(traceback.format_exc())
            return False

    def get_certificate_path(self) -> Optional[Path | str]:
        """
        Get path of received CA certificate.

        Returns:
            Path to received certificate or None if not received yet
        """
        if self.has_certificate():
            return self._cert_manager.get_ca_cert_path(
                source_id=self.config.get_server_uid()
            )
        return None

    # ==================== Stream Management ====================

    async def enable_stream(self, stream_type: int) -> None:
        """
        Enable a specific stream type (applies before start or at runtime)

        Raises:
            ValueError: If the stream type is invalid
        """
        if not StreamType.is_valid(stream_type):
            raise ValueError(f"Invalid stream type: {stream_type}")

        self.config.enable_stream(stream_type)
        await self.config.save()
        self._logger.info(f"Enabled stream: {stream_type}")

    async def disable_stream(self, stream_type: int) -> None:
        """
        Disable a specific stream type (applies before start or at runtime)
        Raises:
            ValueError: If the stream type is invalid
        """
        if not StreamType.is_valid(stream_type):
            raise ValueError(f"Invalid stream type: {stream_type}")

        if StreamType.COMMAND == stream_type:
            self._logger.warning(
                "Command stream is always enabled and cannot be disabled"
            )
            return

        self.config.disable_stream(stream_type)
        await self.config.save()
        self._logger.info(f"Disabled stream: {stream_type}")

    def is_stream_enabled(self, stream_type: int) -> bool:
        """Check if a stream is enabled"""
        return self.config.is_stream_enabled(stream_type)

    async def enable_stream_runtime(self, stream_type: int) -> bool:
        """Enable a stream at runtime"""
        if not self._running or not self._connected:
            await self.enable_stream(stream_type)
            return True

        # Se già abilitato, non fare nulla
        if self.is_stream_enabled(stream_type):
            self._logger.warning(f"Stream {stream_type} already enabled")
            return True

        # Abilita nella configurazione
        await self.enable_stream(stream_type)

        # Inizializza e avvia i componenti per questo stream
        try:
            if stream_type == StreamType.MOUSE:
                await self._enable_mouse_stream()
            elif stream_type == StreamType.KEYBOARD:
                await self._enable_keyboard_stream()
            elif stream_type == StreamType.CLIPBOARD:
                await self._enable_clipboard_stream()
            else:
                self._logger.error(f"Unknown stream type: {stream_type}")
                return False

            self._logger.info(f"Runtime enabled stream: {stream_type}")
            return True
        except Exception as e:
            await self.disable_stream(stream_type)
            self._logger.error(f"Failed to enable {stream_type} stream: {e}")
            raise RuntimeError(f"Failed to enable {stream_type} stream: {e}")

    async def disable_stream_runtime(self, stream_type: int) -> bool:
        """Disable a stream at runtime"""
        # If client is connected and running, we don't provide runtime enabling
        if self._running and self._connected:
            self._logger.error(
                "Cannot disable streams at runtime while connected to server"
            )
            return False

        if not self._running or not self.is_stream_enabled(stream_type):
            await self.disable_stream(stream_type)
            return True

        # Disabilita nella configurazione
        await self.disable_stream(stream_type)

        # Ferma e rimuovi i componenti
        try:
            if stream_type == StreamType.MOUSE:
                await self._disable_mouse_stream()
            elif stream_type == StreamType.KEYBOARD:
                await self._disable_keyboard_stream()
            elif stream_type == StreamType.CLIPBOARD:
                await self._disable_clipboard_stream()
            else:
                self._logger.error(f"Unknown stream type: {stream_type}")
                return False

            self._logger.info(f"Runtime disabled stream: {stream_type}")
            return True
        except Exception as e:
            self._logger.error(f"Failed to disable {stream_type} stream: {e}")
            raise RuntimeError(f"Failed to disable {stream_type} stream: {e}")

    # ==================== Client Lifecycle ====================

    async def check_server_availability(self) -> bool:
        """
        Check if server is registered as available via mDNS.
        If no services are discovered yet, it will perform discovery first.

        Returns:
            bool: True if server is available, False otherwise
        """
        if self._found_services is None or len(self._found_services) == 0:
            await self.discover_servers()
            await asyncio.sleep(0)

        if (
            self.config.get_server_uid() is None
            or self.config.get_server_host() is None
            or self.config.get_server_port() is None
        ):
            return False

        for service in self._found_services:
            # We need to match all (address and port could vary)
            if (
                service.uid == self.config.get_server_uid()
                and service.port == self.config.get_server_port()
            ):
                # If hostname is present, verify it first
                if service.hostname:
                    if service.hostname == self.config.get_server_host():
                        return True

                # if no hostname, verify address
                elif service.address == self.config.get_server_host():
                    return True

        return False

    async def server_choice_needed(self) -> bool:
        """
        Check if we have to choose a server from discovered services.

        Returns:
            True if server choice is needed, False otherwise
        """
        async with self._state_lock:
            return await self._need_server_choice

    def get_found_servers(self) -> list[Service]:
        """
        Get the list of discovered servers.

        Returns:
            List of discovered services
        """
        return self._found_services

    def choose_server(self, uid: str) -> None:
        """
        Choose a server from discovered services by UID.
        It will set the server host and port in the configuration.
        Args:
            uid: UID of the server to choose
        """
        for service in self._found_services:
            if service.uid == uid:
                self.config.set_server_connection(
                    uid=service.uid,
                    host=service.address,
                    hostname=service.hostname,
                    port=service.port,
                )
                # Set the server future result in case someone is waiting for it
                if not self._server.done():
                    self._server.set_result(service)

                return

        self._logger.error("Server not found among discovered services", uid=uid)

    async def discover_servers(self) -> None:
        """
        Discover available servers on the network using mDNS.

        Returns:
            List of discovered servers with their details
        """
        try:
            self._found_services = await self._mdns_service.discover_services()
        except Exception as e:
            self._logger.error(f"Error during server discovery -> {e}")

    async def _handle_certificate_check(self) -> Optional[str]:
        """
        Handles the process of checking and obtaining a server certificate if necessary.

        This method ensures that a valid SSL certificate is available before proceeding with
        SSL-related operations. If a certificate is missing, it triggers the process to receive
        a new certificate using an OTP provided by the server.

        Returns:
            Optional[str]: The path to the CA certificate if available or successfully obtained;
            otherwise, None.
        """
        try:
            if self.config.ssl_enabled and not self.has_certificate():
                certfile = self._cert_manager.get_ca_cert_path(
                    source_id=self.config.get_server_uid(),
                )

                # If still not found,force start certificate receiving process
                if not certfile:
                    self._otp_needed.set_result(True)
                    self._logger.info(
                        "Waiting to receive certificate from server. Provide OTP to continue..."
                    )

                    otp = await self._otp_received
                    if not await self.receive_certificate(otp=otp):
                        return None

            return self._cert_manager.get_ca_cert_path(
                source_id=self.config.get_server_uid(),
            )
        except Exception as e:
            self._logger.error(f"Error during certificate check -> {e}")
            return None
        finally:
            if not self._otp_needed.done():
                self._otp_needed.set_result(False)
            else:
                async with self._state_lock:
                    self._otp_needed = asyncio.Future()  # Reset for future use

    async def _handle_server_availability(self) -> bool:
        """
        Checks and handles the availability of servers asynchronously.

        This method verifies whether a previously saved server is still available. If it is not, the
        method will evaluate other available servers identified on the network. Based on the number
        of discovered servers, it will either auto-select the only available server or wait the user
        to choose one. It will then proceed with the user's choice. If any exception occurs during
        execution, it logs an error message. Finally, it ensures the resolution of the need for server
        choice.

        Returns:
            bool: True if server availability check completes successfully, False if no server is found.
        """
        try:
            if not await self.check_server_availability():
                self._logger.warning("Saved server not found")

                if not self._found_services or len(self._found_services) == 0:
                    self._logger.warning(
                        "Cannot find any available servers on the network"
                    )
                    return False

                if len(self._found_services) == 1:
                    # Auto choose the only available server
                    self.choose_server(self._found_services[0].uid)
                    self._need_server_choice.set_result(False)
                else:
                    self._logger.info(
                        "Multiple servers found. Please choose one to connect.",
                        servers=[s.as_dict() for s in self._found_services],
                    )
                    self._need_server_choice.set_result(True)

                # If server not found, we wait the user to choose it and set the future
                res = await self._server

                self._logger.info(
                    "Server chosed",
                    uid=res.uid,
                    host=res.address,
                    hostname=res.hostname,
                    port=res.port,
                )

            return True
        except Exception as e:
            self._logger.error(f"Error during server availability check -> {e}")
            return False
        finally:
            if not self._need_server_choice.done():
                self._need_server_choice.set_result(False)
            else:
                async with self._state_lock:
                    self._need_server_choice = asyncio.Future()  # Reset for future use

    async def start(self) -> bool:
        """Start the client and connect to server"""
        if self._running:
            self._logger.warning("Client already running")
            return False

        self._logger.info("Starting Client...")

        # Initial server availability check
        if not await self._handle_server_availability():
            return False

        # Initialize stream handlers (but don't start them yet)
        try:
            await self._initialize_streams()
        except Exception as e:
            self._logger.error(f"Failed to initialize streams: {e}")
            return False

        # Get enabled streams
        enabled_streams = self._get_enabled_stream_types()

        # Get certificate path if SSL is enabled
        certfile = await self._handle_certificate_check()

        # Initialize connection handler
        self.connection_handler = ConnectionHandler(
            connected_callback=self._on_connected,
            disconnected_callback=self._on_disconnected,
            reconnected_callback=self._on_streams_reconnected,
            host=self.config.get_server_host(),
            port=self.config.get_server_port(),
            heartbeat_interval=self.config.get_heartbeat_interval(),
            clients=self.clients_manager,
            open_streams=enabled_streams,
            auto_reconnect=self.config.do_auto_reconnect(),
            certfile=certfile,
        )

        # Connect to server
        if not await self.connection_handler.start():
            self._logger.error("Failed to connect to server")
            return False

        self._running = True
        server_host = self.config.get_server_host()
        server_port = self.config.get_server_port()
        self._logger.info(
            f"Client started and connecting to {server_host}:{server_port}"
        )
        return True

    async def stop(self):
        """Stop all client components"""
        if not self._running:
            self._logger.warning("Client not running")
            return

        self._logger.info("Stopping Client...")

        # Stop all components
        for component_name, component in list(self._components.items()):
            try:
                if hasattr(component, "stop"):
                    if asyncio.iscoroutinefunction(component.stop):
                        await component.stop()
                    else:
                        component.stop()
            except Exception as e:
                self._logger.error(f"Error stopping component {component_name}: {e}")

        # Stop all stream handlers
        for stream_type, handler in list(self._stream_handlers.items()):
            try:
                if hasattr(handler, "stop"):
                    await handler.stop()
            except Exception as e:
                self._logger.error(f"Error stopping stream handler {stream_type}: {e}")

        # Disconnect from server
        if self.connection_handler:
            await self.connection_handler.stop()

        # Wait a moment to ensure everything is cleaned up
        await asyncio.sleep(self.CLEANUP_DELAY)
        # Cleanup resources
        self.cleanup()
        self._running = False
        self._connected = False
        self._logger.info("Client stopped")

    def cleanup(self):
        """Cleanup client resources"""
        self._logger.info("Cleaning up client resources...")
        # We cleanup component and stream handlers obj from memory
        self._components.clear()
        self._stream_handlers.clear()

        # We also reset event bus
        self.event_bus = AsyncEventBus()
        self._logger.info("Client resources cleaned up.")

    def is_running(self) -> bool:
        """Check if client is running"""
        return self._running

    def is_connected(self) -> bool:
        """Check if connected to server"""
        return self._connected

    # ==================== Private Initialization Methods ====================

    def _get_enabled_stream_types(self) -> list[int]:
        """Get list of enabled stream types for connection"""
        enabled: list[int] = [StreamType.COMMAND]  # Command is always enabled
        for stream_type, is_enabled in self.config.streams_enabled.items():
            if is_enabled and stream_type != StreamType.COMMAND:
                enabled.append(stream_type)
        return enabled

    async def _initialize_streams(self):
        """Initialize stream handlers (don't start them yet - will start on connection)"""
        # Command stream (always required)
        self._stream_handlers[StreamType.COMMAND] = BidirectionalStreamHandler(
            stream_type=StreamType.COMMAND,
            clients=self.clients_manager,
            event_bus=self.event_bus,
            handler_id="ClientCommandStreamHandler",
            active_only=True,  # We send commands only when client is active
        )
        # Force enable command stream
        await self.enable_stream(StreamType.COMMAND)

        # Mouse stream (receiver)
        self._stream_handlers[StreamType.MOUSE] = UnidirectionalStreamHandler(
            stream_type=StreamType.MOUSE,
            clients=self.clients_manager,
            event_bus=self.event_bus,
            handler_id="ClientMouseStreamHandler",
            sender=False,  # Client receives mouse data
            active_only=True,  # Only when client is active
            buffer_size=10000,  # Larger buffer for high-frequency mouse data
        )

        # Keyboard stream (receiver)
        self._stream_handlers[StreamType.KEYBOARD] = UnidirectionalStreamHandler(
            stream_type=StreamType.KEYBOARD,
            clients=self.clients_manager,
            event_bus=self.event_bus,
            handler_id="ClientKeyboardStreamHandler",
            sender=False,  # Client receives keyboard data
            active_only=True,  # Only when client is active
        )

        # Clipboard stream (bidirectional)
        self._stream_handlers[StreamType.CLIPBOARD] = BidirectionalStreamHandler(
            stream_type=StreamType.CLIPBOARD,
            clients=self.clients_manager,
            event_bus=self.event_bus,
            handler_id="ClientClipboardStreamHandler",
            active_only=False,  # Clipboard may be active even if client is inactive
        )

    async def _initialize_components(self):
        """Initialize enabled components based on configuration"""
        # Initialize stream components based on enabled streams
        await self._enable_command_stream()
        await self._enable_mouse_stream()
        await self._enable_keyboard_stream()
        await self._enable_clipboard_stream()

    # ==================== Runtime Enable/Disable Methods ====================

    async def _ensure_stream_active(self, stream_type: int, stream_handler) -> None:
        """Helper: Ensure stream handler is started if enabled"""
        is_enabled = self.is_stream_enabled(stream_type)
        if is_enabled and not stream_handler.is_active():
            if not await stream_handler.start():
                raise RuntimeError(f"Failed to start stream handler for {stream_type}")

    async def _enable_command_stream(self):
        """Enable command stream and components at runtime"""
        # Command stream is always enabled
        command_stream = self._stream_handlers.get(StreamType.COMMAND)
        if not command_stream:
            self._logger.error("Command stream handler not initialized")
            return

        # Start stream if connected
        if self._connected:
            await self._ensure_stream_active(StreamType.COMMAND, command_stream)

        # Command Handler - handles incoming commands from server
        command_handler = self._components.get("command_handler")
        if not command_handler:
            command_handler = CommandHandler(
                event_bus=self.event_bus, stream=command_stream
            )
            self._components["command_handler"] = command_handler

    async def _enable_mouse_stream(self):
        """Enable mouse stream and components at runtime"""
        # Get stream handler
        mouse_stream = self._stream_handlers.get(StreamType.MOUSE)
        if not mouse_stream:
            self._logger.error("Mouse stream handler not initialized")
            return

        # Start stream if enabled and connected
        if self._connected:
            await self._ensure_stream_active(StreamType.MOUSE, mouse_stream)

        is_enabled = self.is_stream_enabled(StreamType.MOUSE)
        command_stream = self._stream_handlers[StreamType.COMMAND]

        # Mouse Controller - handles incoming mouse events from server
        mouse_controller = self._components.get("mouse_controller")
        if not mouse_controller:
            mouse_controller = ClientMouseController(
                event_bus=self.event_bus,
                stream_handler=mouse_stream,
                command_stream=command_stream,
            )
            if is_enabled and self._connected:
                await mouse_controller.start()
            self._components["mouse_controller"] = mouse_controller
        elif is_enabled and self._connected and not mouse_controller.is_alive():
            await mouse_controller.start()

    async def _disable_mouse_stream(self):
        """Disable mouse stream and components at runtime"""
        # Stop mouse controller
        mouse_controller = self._components.get("mouse_controller")
        if mouse_controller:
            await mouse_controller.stop()

        # Stop stream handler
        mouse_stream = self._stream_handlers.get(StreamType.MOUSE)
        if mouse_stream:
            await mouse_stream.stop()

    async def _enable_keyboard_stream(self):
        """Enable keyboard stream and components at runtime"""
        # Get stream handler
        keyboard_stream = self._stream_handlers.get(StreamType.KEYBOARD)
        if not keyboard_stream:
            self._logger.error("Keyboard stream handler not initialized")
            return

        # Start stream if enabled and connected
        if self._connected:
            await self._ensure_stream_active(StreamType.KEYBOARD, keyboard_stream)

        is_enabled = self.is_stream_enabled(StreamType.KEYBOARD)
        command_stream = self._stream_handlers[StreamType.COMMAND]

        # Keyboard Controller - handles incoming keyboard events from server
        keyboard_controller = self._components.get("keyboard_controller")
        if not keyboard_controller:
            keyboard_controller = ClientKeyboardController(
                event_bus=self.event_bus,
                stream_handler=keyboard_stream,
                command_stream=command_stream,
            )
            if is_enabled and self._connected:
                await keyboard_controller.start()
            self._components["keyboard_controller"] = keyboard_controller
        elif is_enabled and self._connected and not keyboard_controller.is_alive():
            await keyboard_controller.start()

    async def _disable_keyboard_stream(self):
        """Disable keyboard stream and components at runtime"""
        # Stop keyboard controller
        keyboard_controller = self._components.get("keyboard_controller")
        if keyboard_controller:
            await keyboard_controller.stop()

        # Stop stream handler
        keyboard_stream = self._stream_handlers.get(StreamType.KEYBOARD)
        if keyboard_stream:
            await keyboard_stream.stop()

    async def _enable_clipboard_stream(self):
        """Enable clipboard stream and components at runtime"""
        # Get stream handler
        clipboard_stream = self._stream_handlers.get(StreamType.CLIPBOARD)
        if not clipboard_stream:
            self._logger.error("Clipboard stream handler not initialized")
            return

        # Start stream if enabled and connected
        if self._connected:
            await self._ensure_stream_active(StreamType.CLIPBOARD, clipboard_stream)

        is_enabled = self.is_stream_enabled(StreamType.CLIPBOARD)
        command_stream = self._stream_handlers[StreamType.COMMAND]

        # Clipboard Listener - monitors clipboard changes and sends to server
        clipboard_listener = self._components.get("clipboard_listener")
        if not clipboard_listener:
            clipboard_listener = ClipboardListener(
                event_bus=self.event_bus,
                stream_handler=clipboard_stream,
                command_stream=command_stream,
            )
            if is_enabled and self._connected:
                await clipboard_listener.start()
            self._components["clipboard_listener"] = clipboard_listener
        elif is_enabled and self._connected and not clipboard_listener.is_alive():
            await clipboard_listener.start()

        # Clipboard Controller - handles incoming clipboard updates from server
        clipboard_controller = self._components.get("clipboard_controller")
        if not clipboard_controller and clipboard_listener:
            clipboard_controller = ClipboardController(
                event_bus=self.event_bus,
                clipboard=clipboard_listener.get_clipboard_context(),
                stream_handler=clipboard_stream,
            )
            self._components["clipboard_controller"] = clipboard_controller

    async def _disable_clipboard_stream(self):
        """Disable clipboard stream and components at runtime"""
        # Stop clipboard listener
        clipboard_listener = self._components.get("clipboard_listener")
        if clipboard_listener:
            await clipboard_listener.stop()

        # Stop stream handler
        clipboard_stream = self._stream_handlers.get(StreamType.CLIPBOARD)
        if clipboard_stream:
            await clipboard_stream.stop()

    # ==================== Event Callbacks ====================

    async def _on_connected(self, client: ClientObj):
        """Handle connection to server event"""
        self._connected = True
        # Initialize components
        await self._initialize_components()

        # Start all enabled stream handlers
        for stream_type, handler in self._stream_handlers.items():
            if self.is_stream_enabled(stream_type):
                if not await handler.start():
                    self._logger.error(f"Failed to start stream handler: {stream_type}")

        # Dispatch event
        await self.event_bus.dispatch(event_type=EventType.CLIENT_ACTIVE, data=None)

        await self.save_config()

        self._logger.info(f"Connected to server at {client.get_net_id()}")

    async def _on_disconnected(self, client: ClientObj):
        """Handle disconnection from server event"""
        self._connected = False

        # Dispatch event
        await self.event_bus.dispatch(event_type=EventType.CLIENT_INACTIVE, data=None)

        # Stop all stream handlers
        for stream_type, handler in list(self._stream_handlers.items()):
            try:
                await handler.stop()
            except Exception as e:
                self._logger.error(f"Error stopping stream handler {stream_type}: {e}")

        # Stop all components
        for component_name, component in list(self._components.items()):
            try:
                if hasattr(component, "stop"):
                    if asyncio.iscoroutinefunction(component.stop):
                        await component.stop()
                    else:
                        component.stop()
            except Exception as e:
                self._logger.error(f"Error stopping component {component_name}: {e}")

        self._logger.info(f"Disconnected from server at {client.get_net_id()}")

    async def _on_streams_reconnected(self, client: ClientObj, streams: list[int]):
        """Handle streams reconnected event"""
        self._logger.info("Streams reconnected", streams=streams)
        await self.event_bus.dispatch(
            event_type=EventType.CLIENT_STREAM_RECONNECTED,
            data=ClientStreamReconnectedEvent(
                client_screen=client.get_screen_position(), streams=streams
            ),
        )

    # ==================== Utility Methods ====================

    def get_event_bus(self) -> AsyncEventBus:
        """Get the event bus instance"""
        return self.event_bus

    def get_stream_handler(self, stream_type: int) -> Optional[object]:
        """Get a specific stream handler"""
        return self._stream_handlers.get(stream_type)

    def get_component(self, component_name: str) -> Optional[object]:
        """Get a specific component by name"""
        return self._components.get(component_name)

    def get_enabled_streams(self) -> list[int]:
        """Get list of enabled stream types"""
        return [k for k, v in self.config.streams_enabled.items() if v]

    def get_active_streams(self) -> list[int]:
        """Get list of currently active stream types"""
        return [
            st for st, handler in self._stream_handlers.items() if handler.is_active()
        ]

    async def start_metrics_collection(self):
        """Start metrics collection"""
        await self._performance_monitor.start()

    async def stop_metrics_collection(self):
        """Stop metrics collection"""
        await self._performance_monitor.stop()
