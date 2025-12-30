"""
Unified Server API
Provides a clean interface to configure and manage server components.
"""

import asyncio
import socket

from typing import Optional, Dict, Tuple

from config import ApplicationConfig, ServerConfig
from model.client import ClientObj, ClientsManager, ScreenPosition
from event.bus import AsyncEventBus
from event import (
    EventType,
    ClientConnectedEvent,
    ClientDisconnectedEvent,
    ClientStreamReconnectedEvent,
)
from network.connection.server import ConnectionHandler
from network.stream.handler.server import (
    UnidirectionalStreamHandler,
    BidirectionalStreamHandler,
    MulticastStreamHandler,
)
from network.stream import StreamType
from network.stream.handler import StreamHandler

from command import CommandHandler

from input.cursor import CursorHandlerWorker
from input.mouse import ServerMouseListener, ServerMouseController
from input.keyboard import ServerKeyboardListener
from input.clipboard import ClipboardListener, ClipboardController

from utils.metrics import MetricsCollector, PerformanceMonitor
from utils.net import get_local_ip
from utils.crypto import CertificateManager
from utils.crypto.sharing import CertificateSharing

from utils.logging import Logger, get_logger

from . import ServiceDiscovery


class Server:
    """
    Manages server configurations, clients, connections, SSL setup, and certificate sharing.

    This class provides features for configuring the server, managing client connections,
    enabling and disabling SSL, managing SSL certificates, sharing certificates securely,
    and maintaining an allowlist of clients. It abstracts away the complexities involved
    in handling connections, certificate generation, and client management.
    """

    CLEANUP_DELAY = 0.5  # seconds to wait during cleanup

    def __init__(
        self,
        app_config: Optional[ApplicationConfig] = None,
        server_config: Optional[ServerConfig] = None,
        auto_load_config: bool = True,
    ):
        """
        Initializes the primary configuration and components of the server application.

        The constructor initializes core configurations, logging, components, and
        registries required for the server application. It loads application-specific
        settings, manages secure connections with optional SSL certificates, and sets
        up core communication components and registries for managing client connections
        and event handling.

        Args:
            app_config: The application-level settings such as directory paths and
                app-specific preferences. Defaults to None, in which case a default
                configuration is initialized.
            server_config: The server's runtime configuration including connection,
                SSL, logging, streams, and authorized clients. Defaults to None, using
                a default configuration.
            auto_load_config: If True, automatically loads configuration from file if exists.
                Defaults to True.

        Attributes:
            _logger (Logger): Internal logger for managing application logs.
            app_config (ApplicationConfig): Initialized or passed application
                configuration object.
            config (ServerConfig): Holds all server settings including connection,
                SSL, logging, streams, and authorized clients.
            _cert_manager (CertificateManager): Manages SSL certificates for secure
                connections.
            _cert_sharing (Optional[CertificateSharing]): Facilitates certificate
                sharing between components, initialized only in SSL mode. Defaults to
                None.
            clients_manager (ClientsManager): Handles and tracks currently connected
                clients.
            event_bus (AsyncEventBus): Manages asynchronous events and their
                distribution across the application services.
            _stream_handlers (Dict[int, StreamHandler]): Registry mapping stream
                identifiers to their respective handlers.
            _components (dict): Storage for application components or services
                initialized during runtime by the server.
            _running (bool): Indicates the server's running state. Initialized as
                False.
            connection_handler (Optional[ConnectionHandler]): Coordinates incoming and
                outgoing connections. Defaults to None, and is set during runtime.

        Raises:
            The constructor does not explicitly raise exceptions but may encounter
            errors indirectly if components initialization or configurations fail.
        """
        # Initialize configurations
        self.app_config = app_config or ApplicationConfig()
        self.config = server_config or ServerConfig(self.app_config)

        # Try to load existing configuration if requested
        if auto_load_config:
            self.config.sync_load()

        # Set logging level
        self._logger = get_logger(
            self.__class__.__name__, level=self.config.log_level, is_root=True
        )

        # Log loaded clients
        self._load_authorized_clients()

        # Initialize certificate manager
        self._cert_manager = CertificateManager(
            cert_dir=self.app_config.get_certificate_path()
        )
        self._cert_sharing: Optional[CertificateSharing] = None

        # Setup SSL if enabled
        self.certfile, self.keyfile = None, None
        if self.config.ssl_enabled:
            self.certfile, self.keyfile = self._setup_certificates()

        # Initialize event bus
        self.event_bus = AsyncEventBus()

        # Stream handlers registry
        self._stream_handlers: Dict[int, StreamHandler] = {}

        # Components registry
        self._components = {}
        self._running = False

        # Metrics and performance monitoring
        self._metrics_collector = MetricsCollector()
        self._performance_monitor = PerformanceMonitor(self._metrics_collector)

        # mDNS
        self._mdns_service = ServiceDiscovery()

        # Connection handler
        self.connection_handler: Optional[ConnectionHandler] = None

    @property
    def clients_manager(self) -> ClientsManager:
        """Access the ClientsManager from configuration"""
        return self.config.clients_manager

    # ==================== Configuration Management ====================

    def _load_authorized_clients(self) -> None:
        """Log info about loaded authorized clients (clients are loaded by config)"""
        clients = self.clients_manager.get_clients()
        if len(clients) > 0:
            self._logger.info(
                f"Loaded {len(clients)} authorized clients from configuration"
            )
        else:
            self._logger.info("No authorized clients found in configuration")

    async def save_config(self) -> bool:
        """
        Save current configuration to file.

        Returns:
            True if saved successfully, False otherwise
        """
        try:
            # Clients are already in config.clients_manager, just save
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
                # Clients are already loaded by config
                self._load_authorized_clients()
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
        """
        Enable SSL for server connections. It will take effect on next start.
        We won't auto save config here, need a manual save after this call.
        """
        try:
            self.certfile, self.keyfile = self._setup_certificates()
        except Exception:
            self.config.disable_ssl()
            self._logger.error("Failed to setup SSL certificates, cannot enable SSL")
            return False

        self.config.enable_ssl()
        self._logger.info("SSL enabled for server connections")
        return True

    def disable_ssl(self) -> None:
        """
        Disable SSL for server connections. It will take effect on next start.
        We won't auto save config here, need a manual save after this call.
        """
        self.certfile = None
        self.keyfile = None
        self.config.disable_ssl()
        self._logger.info("SSL disabled for server connections")

    def _setup_certificates(self) -> Tuple[str, str]:
        """
        Ensure SSL certificates are available.

        Returns:
            Tuple of (certfile_path, keyfile_path)
        """
        try:
            if not self._cert_manager.certificates_exist():
                self._logger.warning(
                    "SSL certificates not found, generating new ones..."
                )
                hostname = socket.gethostname()
                ip = get_local_ip()

                self._cert_manager.generate_ca()
                self._cert_manager.generate_server_certificate(
                    hostname, [ip, "localhost"]
                )

                certfile, keyfile = self._cert_manager.get_server_credentials()
                if not certfile or not keyfile:
                    raise RuntimeError("Failed to generate SSL certificates")

                self._logger.info("SSL certificates generated successfully")
                return certfile, keyfile
            else:
                certfile, keyfile = self._cert_manager.get_server_credentials()
                if not certfile or not keyfile:
                    raise RuntimeError("SSL certificates found but failed to load")

                self._logger.info("SSL certificates found and loaded")
                return certfile, keyfile
        except Exception as e:
            self._logger.error(f"Error setting up SSL certificates -> {e}")
            raise

    # TODO: Better handling -> We should keep the sharing server alive because port may be blocked
    async def share_certificate(
        self, host: str = "0.0.0.0", port: int = 55556, timeout: int = 30
    ) -> Tuple[bool, Optional[str]]:
        """
        Start certificate sharing process with OTP.

        Opens a temporary server that clients can connect to receive the CA certificate.
        Returns an OTP that must be used by the client to decrypt the certificate.

        Args:
            host: Host address for temporary server (default: all interfaces)
            port: Port for temporary server (default: 55556)
            timeout: Maximum time window in seconds (default: 10)

        Returns:
            Tuple of (success, otp). OTP is None if failed.

        Example:
        ::
            success, otp = await server.share_certificate()
            if success:
                print(f"Share this OTP with client: {otp}")
        """
        if not self._cert_manager.certificates_exist():
            self._logger.error("No certificates available to share")
            return False, None

        # Stop previous sharing if active
        if self._cert_sharing and self._cert_sharing.is_sharing_active():
            await self._cert_sharing.stop_sharing()

        try:
            # Get CA certificate
            cert_data = self._cert_manager.load_ca_data()

            if not cert_data:
                self._logger.error("Failed to load CA certificate data")
                return False, None

            # Create and start sharing
            self._cert_sharing = CertificateSharing(
                cert_data=cert_data, host=host, port=port, timeout=timeout
            )

            success, otp = await self._cert_sharing.start_sharing()

            if success:
                self._logger.info(f"Certificate sharing started. OTP: {otp}")
                return True, otp
            else:
                self._logger.error("Failed to start certificate sharing")
                return False, None

        except Exception as e:
            self._logger.error(f"Error starting certificate sharing -> {e}")
            return False, None

    async def stop_cert_sharing(self):
        """
        Stop certificate sharing server.

        Invalidates OTP and closes temporary server.
        """
        if self._cert_sharing:
            await self._cert_sharing.stop_sharing()
            self._logger.info("Certificate sharing stopped")

    def get_sharing_otp(self) -> Optional[str]:
        """
        Get current OTP for certificate sharing.

        Returns:
            OTP string if valid and sharing is active, None otherwise
        """
        if self._cert_sharing:
            return self._cert_sharing.get_otp()
        return None

    def is_cert_sharing_active(self) -> bool:
        """Check if certificate sharing is currently active"""
        return self._cert_sharing is not None and self._cert_sharing.is_sharing_active()

    # ==================== Client Management ====================

    async def add_client(
        self,
        ip_address: Optional[str] = None,
        hostname: Optional[str] = None,
        screen_position: str = "top",
        auto_save: bool = True,
    ) -> ClientObj:
        """
        Add a client to the authorized list.

        Args:
            ip_address: IP address of the client
            hostname: Hostname of the client
            screen_position: Screen position relative to server
            auto_save: If True, automatically saves configuration after adding

        Returns:
            The created ClientObj
        """
        client = self.config.add_client(
            ip_address=ip_address, hostname=hostname, screen_position=screen_position
        )

        if auto_save:
            await self.save_config()

        self._logger.info(
            f"Added client {ip_address if ip_address else hostname} at position {screen_position}"
        )
        return client

    async def remove_client(
        self,
        ip_address: Optional[str] = None,
        hostname: Optional[str] = None,
        screen_position: Optional[str] = None,
        auto_save: bool = True,
    ) -> bool:
        """
        Remove a client from the authorized list.

        Args:
            ip_address: IP address of the client
            hostname: Hostname of the client
            screen_position: Screen position of the client
            auto_save: If True, automatically saves configuration after removal

        Returns:
            True if client was removed, False if not found
        """
        client = self.config.get_client(
            ip_address=ip_address, hostname=hostname, screen_position=screen_position
        )
        if client:
            if (
                self._running and self.connection_handler is not None
            ):  # If server is running, disconnect client first
                await self.connection_handler.force_disconnect_client(client)
            # Finally remove from allowlist
            self.config.remove_client(client=client)

            if auto_save:
                await self.save_config()

            self._logger.info(
                f"Removed client {ip_address or hostname or screen_position}"
            )
            return True
        return False

    def get_clients(self) -> list[ClientObj]:
        """Get all registered clients"""
        return self.config.get_clients()

    def get_client(
        self,
        ip_address: Optional[str] = None,
        hostname: Optional[str] = None,
        screen_position: Optional[str] = None,
    ) -> Optional[ClientObj]:
        """Get a specific client"""
        return self.config.get_client(
            ip_address=ip_address, hostname=hostname, screen_position=screen_position
        )

    async def edit_client(
        self,
        ip_address: Optional[str] = None,
        hostname: Optional[str] = None,
        old_screen_position: Optional[str] = None,
        new_screen_position: Optional[str] = None,
        auto_save: bool = True,
    ) -> ClientObj:
        """
        Edit a client's properties.

        Args:
            ip_address: IP address of the client to edit
            hostname: Hostname of the client to edit
            new_screen_position: New screen position
            auto_save: If True, automatically saves configuration after editing

        Returns:
            The updated ClientObj
        """
        client = self.config.get_client(
            ip_address=ip_address,
            hostname=hostname,
            screen_position=old_screen_position,
        )
        if not client:
            raise ValueError(f"Client [IP {ip_address}, Host {hostname}] not found")

        # if client is connected do not allow changing screen_position
        if client.is_connected:
            raise RuntimeError("Cannot edit a connected client's properties")

        if new_screen_position:
            client.screen_position = new_screen_position

        self.clients_manager.update_client(client)

        if auto_save:
            await self.save_config()

        self._logger.info(
            f"Edited client {ip_address}: screen_position={new_screen_position}"
        )
        return client

    def is_client_alive(
        self, ip_address: Optional[str] = None, hostname: Optional[str] = None
    ) -> bool:
        """Check if a client is currently connected"""
        if not ip_address and not hostname:
            raise ValueError("Either ip_address or hostname must be provided")

        client = self.config.get_client(ip_address=ip_address, hostname=hostname)
        return client.is_connected if client else False

    async def clear_clients(self, auto_save: bool = True) -> None:
        """
        Remove all clients from authorized list.

        Args:
            auto_save: If True, automatically saves configuration after clearing
        """
        self.config.clients_manager.clear()

        if auto_save:
            await self.save_config()

        self._logger.info("Cleared all clients")

    # ==================== Stream Management ====================

    async def enable_stream(self, stream_type: int) -> None:
        """Enable a specific stream type (applies before start or at runtime)"""
        self.config.enable_stream(stream_type)
        await self.config.save()
        self._logger.info(f"Enabled stream: {stream_type}")

    async def disable_stream(self, stream_type: int) -> None:
        """Disable a specific stream type (applies before start or at runtime)"""
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
        if not self._running:
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
            import traceback

            traceback.print_exc()
            await self.disable_stream(stream_type)
            self._logger.error(f"Failed to enable {stream_type} stream -> {e}")
            raise RuntimeError(f"Failed to enable {stream_type} stream -> {e}")

    async def disable_stream_runtime(self, stream_type: int) -> bool:
        """Disable a stream at runtime"""
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
            self._logger.error(f"Failed to disable {stream_type} stream -> {e}")
            raise RuntimeError(f"Failed to disable {stream_type} stream -> {e}")

    # ==================== Server Lifecycle ====================

    async def start(self) -> bool:
        """Start the server with enabled components"""
        if self._running:
            self._logger.warning("Server already running")
            return False

        self._logger.info("Starting Server...")

        # Initialize and start enabled streams
        try:
            await self._initialize_streams()
        except Exception as e:
            self._logger.error(f"Failed to initialize streams -> {e}")
            await self.stop()
            return False

        # TODO: We should check if port is available before starting by using ServiceDiscovery utils
        # Initialize connection handler
        self.connection_handler = ConnectionHandler(
            connected_callback=self._on_client_connected,
            disconnected_callback=self._on_client_disconnected,
            reconnected_callback=self._on_client_stream_reconnected,
            host=self.config.host,
            port=self.config.port,
            heartbeat_interval=self.config.heartbeat_interval,
            allowlist=self.clients_manager,
            certfile=self.certfile,
            keyfile=self.keyfile,
        )

        try:
            # Start mDNS service
            await self._mdns_service.register_service(
                host=self.config.host, port=self.config.port, uid=self.config.uid
            )
            if (
                self.config.uid is None
            ):  # At this point we have a UID generated and assigned ^
                self.config.uid = self._mdns_service.get_uid()
                await self.save_config()
        except Exception as e:
            self._logger.error(f"Failed to start mDNS service -> {e}")
            await self.stop()
            return False

        # Initialize and start enabled components
        try:
            await self._initialize_components()
        except Exception as e:
            self._logger.error(f"Failed to initialize components -> {e}")
            await self.stop()
            return False

        if not await self.connection_handler.start():
            self._logger.error("Failed to start connection handler")
            await self.stop()
            return False

        self._running = True
        self._logger.info(f"Server started on {self.config.host}:{self.config.port}")
        return True

    async def stop(self):
        """Stop all server components"""
        if not self._running:
            self._logger.warning("Server not running")
            return

        self._logger.info("Stopping Server...")

        # Stop connection handler
        if self.connection_handler:
            await self.connection_handler.stop()

        # Stop all components
        for component_name, component in list(self._components.items()):
            try:
                if hasattr(component, "stop"):
                    if asyncio.iscoroutinefunction(component.stop):
                        await component.stop()
                    else:
                        component.stop()
            except Exception as e:
                self._logger.error(f"Error stopping component {component_name} -> {e}")

        # Stop all stream handlers
        for stream_type, handler in list(self._stream_handlers.items()):
            try:
                if hasattr(handler, "stop"):
                    await handler.stop()
            except Exception as e:
                self._logger.error(
                    f"Error stopping stream handler {stream_type} -> {e}"
                )

        # Stop performance monitor
        await self._performance_monitor.stop()

        # mDNS service unregister
        await self._mdns_service.unregister_service()

        # Wait a moment for cleanup
        await asyncio.sleep(self.CLEANUP_DELAY)

        self.cleanup()
        self._running = False
        self._logger.info("Server stopped")

    def cleanup(self):
        """Cleanup client resources"""
        self._logger.info("Cleaning up resources...")
        # We cleanup component and stream handlers obj from memory
        self._components.clear()
        self._stream_handlers.clear()

        # We also reset event bus
        self.event_bus = AsyncEventBus()
        self._logger.info("Resources cleaned up.")

    def is_running(self) -> bool:
        """Check if server is running"""
        return self._running

    # ==================== Private Initialization Methods ====================

    async def _initialize_streams(self):
        """Initialize stream handlers and start enabled streams"""
        #! We need to create all stream handlers first to allow event listening and state sync

        # Command stream (always required)
        self._stream_handlers[StreamType.COMMAND] = BidirectionalStreamHandler(
            stream_type=StreamType.COMMAND,
            clients=self.clients_manager,
            event_bus=self.event_bus,
            handler_id="ServerCommandStreamHandler",
            metrics_collector=self._metrics_collector,
            do_cleanup=False,  # Command stream should not cleanup on state changes
        )
        # Force start command stream
        if not await self._stream_handlers[StreamType.COMMAND].start():
            raise RuntimeError("Failed to start command stream handler")
        # Add to enabled streams if not present
        if not self.is_stream_enabled(StreamType.COMMAND):
            await self.enable_stream(StreamType.COMMAND)

        # Mouse stream
        self._stream_handlers[StreamType.MOUSE] = UnidirectionalStreamHandler(
            stream_type=StreamType.MOUSE,
            clients=self.clients_manager,
            event_bus=self.event_bus,
            handler_id="ServerMouseStreamHandler",
            sender=True,
            metrics_collector=self._metrics_collector,
        )

        # Keyboard stream
        self._stream_handlers[StreamType.KEYBOARD] = UnidirectionalStreamHandler(
            stream_type=StreamType.KEYBOARD,
            clients=self.clients_manager,
            event_bus=self.event_bus,
            handler_id="ServerKeyboardStreamHandler",
            sender=True,
            metrics_collector=self._metrics_collector,
            buffer_size=10000,
        )

        # Clipboard stream
        self._stream_handlers[StreamType.CLIPBOARD] = MulticastStreamHandler(
            stream_type=StreamType.CLIPBOARD,
            clients=self.clients_manager,
            event_bus=self.event_bus,
            handler_id="ServerClipboardStreamHandler",
            metrics_collector=self._metrics_collector,
        )

        # Start all stream handlers
        for stream_type, handler in self._stream_handlers.items():
            if self.is_stream_enabled(stream_type):
                if not await handler.start():
                    raise RuntimeError(f"Failed to start stream handler: {stream_type}")

    async def _initialize_components(self):
        """Initialize enabled components based on configuration"""
        command_stream = self._stream_handlers[StreamType.COMMAND]

        # Command handler (always required)
        self._components["command_handler"] = CommandHandler(
            event_bus=self.event_bus, stream=command_stream
        )

        # Initialize all stream components (they will start only if enabled)
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

    async def _enable_mouse_stream(self):
        """Enable mouse stream and components at runtime"""
        # Get or create stream handler
        mouse_stream = self._stream_handlers.get(StreamType.MOUSE)
        if not mouse_stream:
            mouse_stream = UnidirectionalStreamHandler(
                stream_type=StreamType.MOUSE,
                clients=self.clients_manager,
                event_bus=self.event_bus,
                handler_id="ServerMouseStreamHandler",
                sender=True,
                buffer_size=10000,  # Higher needed for high polling rates
            )
            self._stream_handlers[StreamType.MOUSE] = mouse_stream

        # Start stream if enabled
        await self._ensure_stream_active(StreamType.MOUSE, mouse_stream)

        is_enabled = self.is_stream_enabled(StreamType.MOUSE)
        command_stream = self._stream_handlers[StreamType.COMMAND]

        # Cursor Handler - manages cursor visibility
        cursor_handler = self._components.get("cursor_handler")
        if not cursor_handler:
            cursor_handler = CursorHandlerWorker(
                event_bus=self.event_bus, stream=mouse_stream, debug=False
            )
            if is_enabled and not cursor_handler.start():
                raise RuntimeError("Failed to start cursor handler")
            self._components["cursor_handler"] = cursor_handler
        elif is_enabled and not cursor_handler.is_alive():
            if not cursor_handler.start():
                await self._disable_mouse_stream()
                raise RuntimeError("Failed to start cursor handler")

        # Mouse Controller - handles incoming mouse commands
        if not self._components.get("mouse_controller"):
            self._components["mouse_controller"] = ServerMouseController(
                event_bus=self.event_bus
            )

        # Mouse Listener - captures and sends mouse events
        mouse_listener = self._components.get("mouse_listener")
        if not mouse_listener:
            mouse_listener = ServerMouseListener(
                event_bus=self.event_bus,
                stream_handler=mouse_stream,
                command_stream=command_stream,
                filtering=False,
            )
            if is_enabled and not mouse_listener.start():
                await self._disable_mouse_stream()
                raise RuntimeError("Failed to start mouse listener")
            self._components["mouse_listener"] = mouse_listener
        elif is_enabled and not mouse_listener.is_alive():
            if not mouse_listener.start():
                await self._disable_mouse_stream()
                raise RuntimeError("Failed to start mouse listener")

    async def _disable_mouse_stream(self):
        """Disable mouse stream and components at runtime"""
        # Stop mouse listener
        mouse_listener = self._components.get("mouse_listener")
        if mouse_listener:
            mouse_listener.stop()

        # Stop stream handler
        mouse_stream = self._stream_handlers.get(StreamType.MOUSE)
        if mouse_stream:
            await mouse_stream.stop()

    async def _enable_keyboard_stream(self):
        """Enable keyboard stream and components at runtime"""
        # Get or create stream handler
        keyboard_stream = self._stream_handlers.get(StreamType.KEYBOARD)
        if not keyboard_stream:
            keyboard_stream = UnidirectionalStreamHandler(
                stream_type=StreamType.KEYBOARD,
                clients=self.clients_manager,
                event_bus=self.event_bus,
                handler_id="ServerKeyboardStreamHandler",
                sender=True,
            )
            self._stream_handlers[StreamType.KEYBOARD] = keyboard_stream

        # Start stream if enabled
        await self._ensure_stream_active(StreamType.KEYBOARD, keyboard_stream)

        is_enabled = self.is_stream_enabled(StreamType.KEYBOARD)
        command_stream = self._stream_handlers[StreamType.COMMAND]

        # Keyboard Listener - captures and sends keyboard events
        keyboard_listener = self._components.get("keyboard_listener")
        if not keyboard_listener:
            keyboard_listener = ServerKeyboardListener(
                event_bus=self.event_bus,
                stream_handler=keyboard_stream,
                command_stream=command_stream,
            )
            if is_enabled and not keyboard_listener.start():
                raise RuntimeError("Failed to start keyboard listener")
            self._components["keyboard_listener"] = keyboard_listener
        elif is_enabled and not keyboard_listener.is_alive():
            if not keyboard_listener.start():
                await self._disable_keyboard_stream()
                raise RuntimeError("Failed to start keyboard listener")

    async def _disable_keyboard_stream(self):
        """Disable keyboard stream and components at runtime"""
        # Stop keyboard listener
        keyboard_listener = self._components.get("keyboard_listener")
        if keyboard_listener:
            keyboard_listener.stop()

        # Stop stream handler
        keyboard_stream = self._stream_handlers.get(StreamType.KEYBOARD)
        if keyboard_stream:
            await keyboard_stream.stop()

    async def _enable_clipboard_stream(self):
        """Enable clipboard stream and components at runtime"""
        # Get or create stream handler
        clipboard_stream = self._stream_handlers.get(StreamType.CLIPBOARD)
        if not clipboard_stream:
            clipboard_stream = MulticastStreamHandler(
                stream_type=StreamType.CLIPBOARD,
                clients=self.clients_manager,
                event_bus=self.event_bus,
                handler_id="ServerClipboardStreamHandler",
            )
            self._stream_handlers[StreamType.CLIPBOARD] = clipboard_stream

        # Start stream if enabled
        await self._ensure_stream_active(StreamType.CLIPBOARD, clipboard_stream)

        is_enabled = self.is_stream_enabled(StreamType.CLIPBOARD)
        command_stream = self._stream_handlers[StreamType.COMMAND]

        # Clipboard Listener - monitors clipboard changes
        clipboard_listener = self._components.get("clipboard_listener")
        if not clipboard_listener:
            clipboard_listener = ClipboardListener(
                event_bus=self.event_bus,
                stream_handler=clipboard_stream,
                command_stream=command_stream,
            )
            if is_enabled and not await clipboard_listener.start():
                raise RuntimeError("Failed to start clipboard listener")
            self._components["clipboard_listener"] = clipboard_listener
        elif is_enabled and not clipboard_listener.is_alive():
            if not await clipboard_listener.start():
                await self._disable_clipboard_stream()
                raise RuntimeError("Failed to start clipboard listener")

        # Clipboard Controller - handles incoming clipboard updates
        if not self._components.get("clipboard_controller"):
            clipboard_controller = ClipboardController(
                event_bus=self.event_bus,
                clipboard=self._components[
                    "clipboard_listener"
                ].get_clipboard_context(),
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

    async def _on_client_connected(self, client: ClientObj, streams: list[int]):
        """Handle client connection event"""
        await self.event_bus.dispatch(
            event_type=EventType.CLIENT_CONNECTED,
            data=ClientConnectedEvent(
                client_screen=client.get_screen_position(), streams=streams
            ),
        )
        # Save config on new connection
        await self.save_config()
        self._logger.info(
            f"Client {client.get_net_id()} connected at position {client.screen_position}"
        )

    async def _on_client_disconnected(self, client: ClientObj, streams: list[int]):
        """Handle client disconnection event"""
        await self.event_bus.dispatch(
            event_type=EventType.CLIENT_DISCONNECTED,
            data=ClientDisconnectedEvent(
                client_screen=client.get_screen_position(), streams=streams
            ),
        )
        await self.save_config()
        self._logger.info(
            f"Client {client.get_net_id()} disconnected from position {client.screen_position}"
        )

    async def _on_client_stream_reconnected(
        self, client: ClientObj, streams: list[int]
    ):
        """Handle client stream reconnection event"""
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
        return list(self._stream_handlers.keys())

    async def start_metrics_collection(self):
        """Start metrics collection"""
        await self._performance_monitor.start()

    async def stop_metrics_collection(self):
        """Stop metrics collection"""
        await self._performance_monitor.stop()


# ==================== Example Usage ====================


async def main():
    """Example usage of PyContinuityServer API with unified ServerConfig"""

    # Create server with unified configuration
    # Option 1: Use default config and configure programmatically
    server = Server()

    # Configure server
    server.config.set_connection_params(host="192.168.1.62", port=5555)
    server.config.set_logging(level=Logger.INFO)

    # Enable streams
    await server.enable_stream(StreamType.MOUSE)
    await server.enable_stream(StreamType.KEYBOARD)
    await server.enable_stream(StreamType.CLIPBOARD)

    # Add clients to authorized list
    await server.add_client(
        ip_address="192.168.1.74", screen_position=ScreenPosition.BOTTOM
    )

    # Save configuration for next time
    await server.save_config()

    # Option 2: Load existing configuration
    # server = Server(auto_load_config=True)

    # Start server
    if not await server.start():
        print("Failed to start server")
        return

    print(f"Server started successfully on {server.config.host}:{server.config.port}")
    print(f"Enabled streams: {server.get_enabled_streams()}")
    print(f"Active streams: {server.get_active_streams()}")
    print(f"Authorized clients: {len(server.get_clients())}")

    try:
        # Keep server running
        await asyncio.sleep(5)

        # Example: Disable mouse at runtime
        print("\nDisabling mouse stream...")
        await server.disable_stream_runtime(StreamType.MOUSE)
        print(f"Active streams: {server.get_active_streams()}")

        await asyncio.sleep(2)

        # Example: Re-enable mouse at runtime
        print("\nRe-enabling mouse stream...")
        await server.enable_stream_runtime(StreamType.MOUSE)
        print(f"Active streams: {server.get_active_streams()}")

        # Keep running
        while True:
            # GUI Hooks or other async tasks can be handled here
            await asyncio.sleep(1)

    except KeyboardInterrupt:
        print("\nKeyboard interrupt received")
    finally:
        print("Stopping server...")
        await server.stop()
        # Save configuration on exit
        await server.save_config()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nServer shutdown complete")
