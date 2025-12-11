"""
Unified Server API for PyContinuity
Provides a clean interface to configure and manage server components.
Supports runtime enable/disable of streams and listeners.
"""
import asyncio
import socket

from typing import Optional, Dict, Tuple

from config import ApplicationConfig, ServerConfig, ServerConnectionConfig
from model.client import ClientObj, ClientsManager, ScreenPosition
from event.bus import AsyncEventBus
from event import EventType
from network.connection.server import ConnectionHandler
from network.stream.server import (
    UnidirectionalStreamHandler,
    BidirectionalStreamHandler,
    MulticastStreamHandler
)
from network.stream import StreamType, StreamHandler
from command import CommandHandler
from input.cursor import CursorHandlerWorker
from input.mouse import ServerMouseListener, ServerMouseController
from input.keyboard import ServerKeyboardListener
from input.clipboard import ClipboardListener, ClipboardController

from utils.net import get_local_ip
from utils.crypto import CertificateManager
from utils.crypto.sharing import CertificateSharing

from utils.logging import Logger,get_logger


class Server:
    """
    Unified Server API for PyContinuity.
    Manages all server components with flexible configuration.

    Features:
    - Start/stop server
    - Manage client whitelist
    - Enable/disable streams at runtime
    - Configure connection parameters
    """

    def __init__(
        self,
        connection_config: Optional[ServerConnectionConfig] = None,
        app_config: Optional[ApplicationConfig] = None,
        server_config: Optional[ServerConfig] = None,
        log_level: int = Logger.INFO
    ):
        # Set logging level
        self._logger = get_logger(self.__class__.__name__)
        self._logger.set_level(log_level)

        # Initialize configurations
        self.app_config = app_config or ApplicationConfig()
        self.server_config = server_config or ServerConfig(self.app_config)
        self.connection_config = connection_config or ServerConnectionConfig()
        self._cert_manager = CertificateManager(cert_dir=self.app_config.get_certificate_path())
        self._cert_sharing: Optional[CertificateSharing] = None
        
        if self.connection_config.ssl_enabled:
            self._setup_certificates()

        # Initialize core components
        self.clients_manager = ClientsManager()
        self.event_bus = AsyncEventBus()

        # Stream handlers registry
        self._stream_handlers: Dict[int, StreamHandler] = {}

        # Components registry
        self._components = {}
        self._running = False

        # Connection handler
        self.connection_handler: Optional[ConnectionHandler] = None

    # ==================== Certificate Management ====================

    def enable_ssl(self) -> bool:
        """Enable SSL for server connections. It will take effect on next start."""
        try:
            self._setup_certificates()
        except Exception:
            self.connection_config.ssl_enabled = False
            self._logger.error("Failed to setup SSL certificates, cannot enable SSL")
            return False

        self.connection_config.ssl_enabled = True
        self._logger.info("SSL enabled for server connections")
        return True

    def disable_ssl(self):
        """Disable SSL for server connections. It will take effect on next start."""
        self.connection_config.ssl_enabled = False
        self.connection_config.certfile = None
        self.connection_config.keyfile = None
        self._logger.info("SSL disabled for server connections")

    def _setup_certificates(self):
        """Ensure SSL certificates are available"""
        try:
            if not self._cert_manager.certificates_exist():
                self._logger.warning("SSL certificates not found, generating new ones...")
                hostname = socket.gethostname()
                ip = get_local_ip()
                
                self._cert_manager.generate_ca()
                self._cert_manager.generate_server_certificate(hostname, [ip, 'localhost'])
                
                self.connection_config.certfile, self.connection_config.keyfile = self._cert_manager.get_server_credentials()
                if not self.connection_config.certfile or not self.connection_config.keyfile:
                    raise RuntimeError("Failed to generate SSL certificates")
                
                self._logger.info("SSL certificates generated successfully")
            else:
                self.connection_config.certfile, self.connection_config.keyfile = self._cert_manager.get_server_credentials()
                self._logger.info("SSL certificates found and loaded")
        except Exception as e:
            self._logger.error(f"Error setting up SSL certificates -> {e}")
            raise

    async def share_certificate(self, host: str = "0.0.0.0", port: int = 5556, timeout: int = 30) -> Tuple[
        bool, Optional[str]]:
        """
        Start certificate sharing process with OTP.

        Opens a temporary server that clients can connect to receive the CA certificate.
        Returns an OTP that must be used by the client to decrypt the certificate.

        Args:
            host: Host address for temporary server (default: all interfaces)
            port: Port for temporary server (default: 5556)
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
                cert_data=cert_data,
                host=host,
                port=port,
                timeout=timeout
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

    def add_client(self, ip_address: Optional[str] = None, hostname: Optional[str] = None, screen_position: str = "top") -> ClientObj:
        """Add a client to the whitelist"""
        client = ClientObj(ip_address=ip_address, screen_position=screen_position, hostname=hostname)
        self.clients_manager.add_client(client)
        self._logger.info(f"Added client {ip_address if ip_address else hostname} at position {screen_position}")
        return client

    async def remove_client(self, ip_address: str = None, hostname: Optional[str] = None, screen_position: str = None) -> bool:
        """Remove a client from the whitelist"""
        client = self.clients_manager.get_client(ip_address=ip_address, screen_position=screen_position, hostname=hostname)
        if client:
            if self._running: # If server is running, disconnect client first
                await self.connection_handler.force_disconnect_client(client)
            # Finally remove from whitelist
            self.clients_manager.remove_client(client)
            self._logger.info(f"Removed client {ip_address or screen_position}")
            return True
        return False

    def get_clients(self) -> list[ClientObj]:
        """Get all registered clients"""
        return self.clients_manager.get_clients()

    def get_client(self, ip_address: Optional[str] = None, hostname: Optional[str] = None, screen_position: str = None) -> Optional[ClientObj]:
        """Get a specific client"""
        return self.clients_manager.get_client(ip_address=ip_address, hostname=hostname, screen_position=screen_position)

    def edit_client(self, ip_address: Optional[str] = None, hostname: Optional[str] = None, screen_position: str = None) -> ClientObj:
        """Edit a client's properties"""
        client = self.clients_manager.get_client(ip_address=ip_address, hostname=hostname)
        if not client:
            raise ValueError(f"Client [IP {ip_address}, Host {hostname}] not found")

        # if client is connected do not allow changing screen_position
        if client.is_connected:
            raise RuntimeError("Cannot edit a connected client's properties")

        if screen_position:
            client.screen_position = screen_position

        self.clients_manager.update_client(client)
        self._logger.info(f"Edited client {ip_address}: screen_position={screen_position}")
        return client

    def is_client_alive(self, ip_address: Optional[str] = None, hostname: Optional[str] = None) -> bool:
        """Check if a client is currently connected"""
        if not ip_address and not hostname:
            raise ValueError("Either ip_address or hostname must be provided")

        client = self.clients_manager.get_client(ip_address=ip_address, hostname=hostname)
        return client.is_connected if client else False

    def clear_clients(self):
        """Remove all clients from whitelist"""
        self.clients_manager.clients.clear()
        self._logger.info("Cleared all clients")

    # ==================== Stream Management ====================

    def enable_stream(self, stream_type: int):
        """Enable a specific stream type (applies before start or at runtime)"""
        self.server_config.enable_stream(stream_type)
        self._logger.info(f"Enabled stream: {stream_type}")

    def disable_stream(self, stream_type: int):
        """Disable a specific stream type (applies before start or at runtime)"""
        if StreamType.COMMAND == stream_type:
            self._logger.warning("Command stream is always enabled and cannot be disabled")
            return
        self.server_config.disable_stream(stream_type)
        self._logger.info(f"Disabled stream: {stream_type}")

    def is_stream_enabled(self, stream_type: int) -> bool:
        """Check if a stream is enabled"""
        return self.server_config.is_stream_enabled(stream_type)

    async def enable_stream_runtime(self, stream_type: int) -> bool:
        """Enable a stream at runtime"""
        if not self._running:
            self.enable_stream(stream_type)
            return True

        # Se già abilitato, non fare nulla
        if self.is_stream_enabled(stream_type):
            self._logger.warning(f"Stream {stream_type} already enabled")
            return True

        # Abilita nella configurazione
        self.enable_stream(stream_type)

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
            self.disable_stream(stream_type)
            self._logger.error(f"Failed to enable {stream_type} stream -> {e}")
            raise RuntimeError(f"Failed to enable {stream_type} stream -> {e}")

    async def disable_stream_runtime(self, stream_type: int) -> bool:
        """Disable a stream at runtime"""
        if not self._running or not self.is_stream_enabled(stream_type):
            self.disable_stream(stream_type)
            return True

        # Disabilita nella configurazione
        self.disable_stream(stream_type)

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

        # Initialize connection handler
        self.connection_handler = ConnectionHandler(
            connected_callback=self._on_client_connected,
            disconnected_callback=self._on_client_disconnected,
            host=self.connection_config.host,
            port=self.connection_config.port,
            heartbeat_interval=self.connection_config.heartbeat_interval,
            allowlist=self.clients_manager,
            certfile=self.connection_config.certfile,
            keyfile=self.connection_config.keyfile,
        )

        if not await self.connection_handler.start():
            self._logger.error("Failed to start connection handler")
            return False

        # Initialize and start enabled streams
        try:
            await self._initialize_streams()
        except Exception as e:
            self._logger.error(f"Failed to initialize streams -> {e}")
            await self.connection_handler.stop()
            return False

        # Initialize and start enabled components
        try:
            await self._initialize_components()
        except Exception as e:
            self._logger.error(f"Failed to initialize components -> {e}")
            await self.stop()
            return False

        self._running = True
        self._logger.info(f"Server started on {self.connection_config.host}:{self.connection_config.port}")
        return True

    async def stop(self):
        """Stop all server components"""
        if not self._running:
            self._logger.warning("Server not running")
            return

        self._logger.info("Stopping PyContinuity Server...")

        # Stop connection handler
        if self.connection_handler:
            await self.connection_handler.stop()

        # Stop all components
        for component_name, component in list(self._components.items()):
            try:
                if hasattr(component, 'stop'):
                    if asyncio.iscoroutinefunction(component.stop):
                        await component.stop()
                    else:
                        component.stop()
            except Exception as e:
                self._logger.error(f"Error stopping component {component_name} -> {e}")

        # Stop all stream handlers
        for stream_type, handler in list(self._stream_handlers.items()):
            try:
                if hasattr(handler, 'stop'):
                    await handler.stop()
            except Exception as e:
                self._logger.error(f"Error stopping stream handler {stream_type} -> {e}")

        self._components.clear()
        self._stream_handlers.clear()
        self._running = False
        self._logger.info("Server stopped")

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
            handler_id="ServerCommandStreamHandler"
        )
        # Force start command stream
        if not await self._stream_handlers[StreamType.COMMAND].start():
            raise RuntimeError("Failed to start command stream handler")
        # Add to enabled streams if not present
        if not self.is_stream_enabled(StreamType.COMMAND):
            self.enable_stream(StreamType.COMMAND)

        # Mouse stream
        self._stream_handlers[StreamType.MOUSE] = UnidirectionalStreamHandler(
            stream_type=StreamType.MOUSE,
            clients=self.clients_manager,
            event_bus=self.event_bus,
            handler_id="ServerMouseStreamHandler",
            sender=True
        )

        # Keyboard stream
        self._stream_handlers[StreamType.KEYBOARD] = UnidirectionalStreamHandler(
            stream_type=StreamType.KEYBOARD,
            clients=self.clients_manager,
            event_bus=self.event_bus,
            handler_id="ServerKeyboardStreamHandler",
            sender=True
        )

        # Clipboard stream
        self._stream_handlers[StreamType.CLIPBOARD] = MulticastStreamHandler(
                stream_type=StreamType.CLIPBOARD,
                clients=self.clients_manager,
                event_bus=self.event_bus,
                handler_id="ServerClipboardStreamHandler"
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
        self._components['command_handler'] = CommandHandler(
            event_bus=self.event_bus,
            stream=command_stream
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
                sender=True
            )
            self._stream_handlers[StreamType.MOUSE] = mouse_stream

        # Start stream if enabled
        await self._ensure_stream_active(StreamType.MOUSE, mouse_stream)

        is_enabled = self.is_stream_enabled(StreamType.MOUSE)
        command_stream = self._stream_handlers[StreamType.COMMAND]

        # Cursor Handler - manages cursor visibility
        cursor_handler = self._components.get('cursor_handler')
        if not cursor_handler:
            cursor_handler = CursorHandlerWorker(
                event_bus=self.event_bus,
                stream=mouse_stream,
                debug=False
            )
            if is_enabled and not cursor_handler.start():
                raise RuntimeError("Failed to start cursor handler")
            self._components['cursor_handler'] = cursor_handler
        elif is_enabled and not cursor_handler.is_alive():
            if not cursor_handler.start():
                await self._disable_mouse_stream()
                raise RuntimeError("Failed to start cursor handler")

        # Mouse Controller - handles incoming mouse commands
        if not self._components.get('mouse_controller'):
            self._components['mouse_controller'] = ServerMouseController(
                event_bus=self.event_bus
            )

        # Mouse Listener - captures and sends mouse events
        mouse_listener = self._components.get('mouse_listener')
        if not mouse_listener:
            mouse_listener = ServerMouseListener(
                event_bus=self.event_bus,
                stream_handler=mouse_stream,
                command_stream=command_stream,
                filtering=False
            )
            if is_enabled and not mouse_listener.start():
                await self._disable_mouse_stream()
                raise RuntimeError("Failed to start mouse listener")
            self._components['mouse_listener'] = mouse_listener
        elif is_enabled and not mouse_listener.is_alive():
            if not mouse_listener.start():
                await self._disable_mouse_stream()
                raise RuntimeError("Failed to start mouse listener")


    async def _disable_mouse_stream(self):
        """Disable mouse stream and components at runtime"""
        # Stop mouse listener
        mouse_listener = self._components.get('mouse_listener')
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
                sender=True
            )
            self._stream_handlers[StreamType.KEYBOARD] = keyboard_stream

        # Start stream if enabled
        await self._ensure_stream_active(StreamType.KEYBOARD, keyboard_stream)

        is_enabled = self.is_stream_enabled(StreamType.KEYBOARD)
        command_stream = self._stream_handlers[StreamType.COMMAND]

        # Keyboard Listener - captures and sends keyboard events
        keyboard_listener = self._components.get('keyboard_listener')
        if not keyboard_listener:
            keyboard_listener = ServerKeyboardListener(
                event_bus=self.event_bus,
                stream_handler=keyboard_stream,
                command_stream=command_stream
            )
            if is_enabled and not keyboard_listener.start():
                raise RuntimeError("Failed to start keyboard listener")
            self._components['keyboard_listener'] = keyboard_listener
        elif is_enabled and not keyboard_listener.is_alive():
            if not keyboard_listener.start():
                await self._disable_keyboard_stream()
                raise RuntimeError("Failed to start keyboard listener")

    async def _disable_keyboard_stream(self):
        """Disable keyboard stream and components at runtime"""
        # Stop keyboard listener
        keyboard_listener = self._components.get('keyboard_listener')
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
                handler_id="ServerClipboardStreamHandler"
            )
            self._stream_handlers[StreamType.CLIPBOARD] = clipboard_stream

        # Start stream if enabled
        await self._ensure_stream_active(StreamType.CLIPBOARD, clipboard_stream)

        is_enabled = self.is_stream_enabled(StreamType.CLIPBOARD)
        command_stream = self._stream_handlers[StreamType.COMMAND]

        # Clipboard Listener - monitors clipboard changes
        clipboard_listener = self._components.get('clipboard_listener')
        if not clipboard_listener:
            clipboard_listener = ClipboardListener(
                event_bus=self.event_bus,
                stream_handler=clipboard_stream,
                command_stream=command_stream
            )
            if is_enabled and not await clipboard_listener.start():
                raise RuntimeError("Failed to start clipboard listener")
            self._components['clipboard_listener'] = clipboard_listener
        elif is_enabled and not clipboard_listener.is_alive():
            if not await clipboard_listener.start():
                await self._disable_clipboard_stream()
                raise RuntimeError("Failed to start clipboard listener")

        # Clipboard Controller - handles incoming clipboard updates
        if not self._components.get('clipboard_controller'):
            clipboard_controller = ClipboardController(
                event_bus=self.event_bus,
                clipboard=self._components['clipboard_listener'].get_clipboard_context(),
                stream_handler=clipboard_stream
            )

            self._components['clipboard_controller'] = clipboard_controller

    async def _disable_clipboard_stream(self):
        """Disable clipboard stream and components at runtime"""
        # Stop clipboard listener
        clipboard_listener = self._components.get('clipboard_listener')
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
            data={"client_screen": client.screen_position, "streams": streams}
        )
        self._logger.info(f"Client {client.get_net_id()} connected at position {client.screen_position}")

    async def _on_client_disconnected(self, client: ClientObj, streams: list[int]):
        """Handle client disconnection event"""
        await self.event_bus.dispatch(
            event_type=EventType.CLIENT_DISCONNECTED,
            data={"client_screen": client.screen_position, "streams": streams}
        )
        self._logger.info(f"Client {client.get_net_id()} disconnected from position {client.screen_position}")

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

    def get_enabled_streams(self) -> list[str]:
        """Get list of enabled stream types"""
        return [k for k, v in self.server_config.streams_enabled.items() if v]

    def get_active_streams(self) -> list[int]:
        """Get list of currently active stream types"""
        return list(self._stream_handlers.keys())


# ==================== Example Usage ====================

async def main():
    """Example usage of PyContinuityServer API"""

    # Create configuration
    conn_config = ServerConnectionConfig(host="192.168.1.62", port=5555)

    # Create server
    server = Server(
        connection_config=conn_config,
        log_level=Logger.INFO
    )


    # Add clients to whitelist
    server.add_client(ip_address="192.168.1.74", screen_position=ScreenPosition.BOTTOM)

    # Start server
    if not await server.start():
        print("Failed to start server")
        return

    print(f"Server started successfully")
    print(f"Enabled streams: {server.get_enabled_streams()}")
    print(f"Active streams: {server.get_active_streams()}")

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


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nServer shutdown complete")

