"""
Unified Client API for PyContinuity
Provides a clean interface to configure and manage client components.
Supports runtime enable/disable of streams and listeners.
"""
import asyncio
import os
from pathlib import Path
from typing import Optional, Dict
from dataclasses import dataclass

from config import ApplicationConfig, ClientConfig
from model.client import ClientObj, ClientsManager
from event.bus import AsyncEventBus
from event import EventType
from network.connection.client import ConnectionHandler
from network.stream.client import (
    UnidirectionalStreamHandler,
    BidirectionalStreamHandler
)
from network.stream import StreamType, StreamHandler
from command import CommandHandler
from input.mouse import ClientMouseController
from input.keyboard import ClientKeyboardController
from input.clipboard import ClipboardListener, ClipboardController
from utils.crypto import CertificateManager
from utils.crypto.sharing import CertificateReceiver
from utils.logging import Logger, get_logger


@dataclass
class ClientConnectionConfig:
    """Client connection configuration"""
    server_host: str = "127.0.0.1"
    server_port: int = 5555
    heartbeat_interval: int = 1
    auto_reconnect: bool = True
    certfile: Optional[str] = None


class Client:
    """
    Unified Client API for PyContinuity.
    Manages all client components with flexible configuration.

    Features:
    - Start/stop client
    - Connect to server
    - Enable/disable streams at runtime
    - Configure connection parameters
    """

    def __init__(
        self,
        connection_config: Optional[ClientConnectionConfig] = None,
        app_config: Optional[ApplicationConfig] = None,
        client_config: Optional[ClientConfig] = None,
        log_level: int = Logger.INFO
    ):
        # Initialize configurations
        self.app_config = app_config or ApplicationConfig()
        self.client_config = client_config or ClientConfig()
        self.connection_config = connection_config or ClientConnectionConfig()
        self._cert_manager = CertificateManager(cert_dir=self.app_config.get_certificate_path())
        self._cert_receiver: Optional[CertificateReceiver] = None

        self._logger = get_logger(self.__class__.__name__)
        self._logger.set_level(log_level)

        # Initialize core components
        self.clients_manager = ClientsManager(client_mode=True)
        self.event_bus = AsyncEventBus()

        # Add server to clients manager
        self.server_client = ClientObj(
            ip_address=self.connection_config.server_host,
            ssl=False
        )
        self.clients_manager.add_client(self.server_client)

        # Stream handlers registry
        self._stream_handlers: Dict[int, StreamHandler] = {}

        # Components registry
        self._components = {}
        self._running = False
        self._connected = False

        # Connection handler
        self.connection_handler: Optional[ConnectionHandler] = None

    # ==================== Certificate Management ====================

    def enable_ssl(self) -> bool:
        """Enable SSL connection if certificate is loaded"""
        if self.connection_config.certfile and os.path.exists(self.connection_config.certfile):
            self._logger.info("SSL connection enabled")
            return True
        else:
            self._logger.warning("Cannot enable SSL: No valid certificate loaded")
            return False

    def disable_ssl(self):
        """Disable SSL connection"""
        self._logger.info("SSL connection disabled")
        self.connection_config.certfile = None

    def _load_certificate(self) -> bool:
        """Load SSL certificate for secure connection"""
        if self._cert_manager.certificate_exist():
            self.connection_config.certfile = self._cert_manager.get_ca_cert_path()
            self._logger.info(f"Loaded certificate from: {self.connection_config.certfile}")
            return True
        else:
            self._logger.warning(f"Certificate not found")
            return False

    def _remove_certificate(self):
        """Remove loaded SSL certificate. It will disable SSL connection."""
        if self.connection_config.certfile:
            self._logger.info(f"Removing certificate and disabling SSL connection")
            self.connection_config.certfile = None
        else:
            self._logger.info("No certificate to remove")

    async def receive_certificate(
            self,
            otp: str,
            server_host: Optional[str] = None,
            server_port: int = 5556,
            timeout: int = 30
    ) -> bool:
        """
        Receive CA certificate from server using OTP.

        Args:
            otp: One-time password provided by server
            server_host: Server host for certificate sharing (default: same as connection host)
            server_port: Server port for certificate sharing (default: 5556)
            save_path: Path to save received certificate (default: ./certs/ca_cert.pem)
            timeout: Connection timeout in seconds (default: 10)

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
            server_host = self.connection_config.server_host

        self._logger.info(f"Attempting to receive certificate from {server_host}:{server_port}")

        try:
            # Create receiver
            self._cert_receiver = CertificateReceiver(
                server_host=server_host,
                server_port=server_port,
                timeout=timeout
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
            if not self._cert_manager.save_ca_data(data=cert_data):
                self._logger.error("Failed to save received certificate")
                return False

            return True

        except Exception as e:
            self._logger.error(f"Error receiving certificate -> {e}")
            import traceback
            self._logger.error(traceback.format_exc())
            return False

    def get_certificate_path(self) -> Optional[Path|str]:
        """
        Get path of received CA certificate.

        Returns:
            Path to received certificate or None if not received yet
        """
        return self.connection_config.certfile

    def has_certificate(self) -> bool:
        """
        Check if client has received a certificate.

        Returns:
            True if certificate was received and file exists
        """
        if not self.connection_config.certfile:
            return False
        return os.path.exists(self.connection_config.certfile)

    # ==================== Stream Management ====================

    def enable_stream(self, stream_type: int):
        """Enable a specific stream type (applies before start or at runtime)"""
        if not hasattr(self.client_config, 'streams_enabled'):
            self.client_config.streams_enabled = {}
        self.client_config.streams_enabled[stream_type] = True
        self._logger.info(f"Enabled stream: {stream_type}")

    def disable_stream(self, stream_type: int):
        """Disable a specific stream type (applies before start or at runtime)"""
        if StreamType.COMMAND == stream_type:
            self._logger.warning("Command stream is always enabled and cannot be disabled")
            return
        if not hasattr(self.client_config, 'streams_enabled'):
            self.client_config.streams_enabled = {}
        self.client_config.streams_enabled[stream_type] = False
        self._logger.info(f"Disabled stream: {stream_type}")

    def is_stream_enabled(self, stream_type: int) -> bool:
        """Check if a stream is enabled"""
        if not hasattr(self.client_config, 'streams_enabled'):
            return False
        return self.client_config.streams_enabled.get(stream_type, False)

    async def enable_stream_runtime(self, stream_type: int) -> bool:
        """Enable a stream at runtime"""
        if not self._running or not self._connected:
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
            self._logger.error(f"Failed to enable {stream_type} stream: {e}")
            raise RuntimeError(f"Failed to enable {stream_type} stream: {e}")

    async def disable_stream_runtime(self, stream_type: int) -> bool:
        """Disable a stream at runtime"""
        # If client is connected and running, we don't provide runtime enabling
        if self._running and self._connected:
            self._logger.error("Cannot disable streams at runtime while connected to server")
            return False

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
            self._logger.error(f"Failed to disable {stream_type} stream: {e}")
            raise RuntimeError(f"Failed to disable {stream_type} stream: {e}")

    # ==================== Client Lifecycle ====================

    async def start(self) -> bool:
        """Start the client and connect to server"""
        if self._running:
            self._logger.warning("Client already running")
            return False

        self._logger.info("Starting Client...")

        # Initialize stream handlers (but don't start them yet)
        try:
            await self._initialize_streams()
        except Exception as e:
            self._logger.error(f"Failed to initialize streams: {e}")
            return False

        # Get enabled streams
        enabled_streams = self._get_enabled_stream_types()

        # Initialize connection handler
        self.connection_handler = ConnectionHandler(
            connected_callback=self._on_connected,
            disconnected_callback=self._on_disconnected,
            host=self.connection_config.server_host,
            port=self.connection_config.server_port,
            heartbeat_interval=self.connection_config.heartbeat_interval,
            clients=self.clients_manager,
            open_streams=enabled_streams,
            auto_reconnect=self.connection_config.auto_reconnect,
            certfile=self.connection_config.certfile,
        )

        # Connect to server
        if not await self.connection_handler.start():
            self._logger.error("Failed to connect to server")
            return False

        self._running = True
        self._logger.info(f"Client started and connecting to {self.connection_config.server_host}:{self.connection_config.server_port}")
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
                if hasattr(component, 'stop'):
                    if asyncio.iscoroutinefunction(component.stop):
                        await component.stop()
                    else:
                        component.stop()
            except Exception as e:
                self._logger.error(f"Error stopping component {component_name}: {e}")

        # Stop all stream handlers
        for stream_type, handler in list(self._stream_handlers.items()):
            try:
                if hasattr(handler, 'stop'):
                    await handler.stop()
            except Exception as e:
                self._logger.error(f"Error stopping stream handler {stream_type}: {e}")

        # Disconnect from server
        if self.connection_handler:
            await self.connection_handler.stop()

        self._components.clear()
        self._stream_handlers.clear()
        self._running = False
        self._connected = False
        self._logger.info("Client stopped")

    def is_running(self) -> bool:
        """Check if client is running"""
        return self._running

    def is_connected(self) -> bool:
        """Check if connected to server"""
        return self._connected

    # ==================== Private Initialization Methods ====================

    def _get_enabled_stream_types(self) -> list[int]:
        """Get list of enabled stream types for connection"""
        if not hasattr(self.client_config, 'streams_enabled'):
            return [StreamType.COMMAND]

        enabled = [StreamType.COMMAND]  # Command is always enabled
        for stream_type, is_enabled in self.client_config.streams_enabled.items():
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
            handler_id="ClientCommandStreamHandler"
        )
        # Force enable command stream
        self.enable_stream(StreamType.COMMAND)

        # Mouse stream (receiver)
        self._stream_handlers[StreamType.MOUSE] = UnidirectionalStreamHandler(
            stream_type=StreamType.MOUSE,
            clients=self.clients_manager,
            event_bus=self.event_bus,
            handler_id="ClientMouseStreamHandler",
            sender=False,  # Client receives mouse data
            active_only=True
        )

        # Keyboard stream (receiver)
        self._stream_handlers[StreamType.KEYBOARD] = UnidirectionalStreamHandler(
            stream_type=StreamType.KEYBOARD,
            clients=self.clients_manager,
            event_bus=self.event_bus,
            handler_id="ClientKeyboardStreamHandler",
            sender=False,  # Client receives keyboard data
            active_only=True
        )

        # Clipboard stream (bidirectional)
        self._stream_handlers[StreamType.CLIPBOARD] = BidirectionalStreamHandler(
            stream_type=StreamType.CLIPBOARD,
            clients=self.clients_manager,
            event_bus=self.event_bus,
            handler_id="ClientClipboardStreamHandler"
        )

    async def _initialize_components(self):
        """Initialize enabled components based on configuration"""
        command_stream = self._stream_handlers[StreamType.COMMAND]

        # Command handler (always required)
        self._components['command_handler'] = CommandHandler(
            event_bus=self.event_bus,
            stream=command_stream
        )

        # Initialize stream components based on enabled streams
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
        mouse_controller = self._components.get('mouse_controller')
        if not mouse_controller:
            mouse_controller = ClientMouseController(
                event_bus=self.event_bus,
                stream_handler=mouse_stream,
                command_stream=command_stream
            )
            if is_enabled and self._connected:
                await mouse_controller.start()
            self._components['mouse_controller'] = mouse_controller
        elif is_enabled and self._connected and not mouse_controller.is_alive():
            await mouse_controller.start()

    async def _disable_mouse_stream(self):
        """Disable mouse stream and components at runtime"""
        # Stop mouse controller
        mouse_controller = self._components.get('mouse_controller')
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
        keyboard_controller = self._components.get('keyboard_controller')
        if not keyboard_controller:
            keyboard_controller = ClientKeyboardController(
                event_bus=self.event_bus,
                stream_handler=keyboard_stream,
                command_stream=command_stream
            )
            if is_enabled and self._connected:
                await keyboard_controller.start()
            self._components['keyboard_controller'] = keyboard_controller
        elif is_enabled and self._connected and not keyboard_controller.is_alive():
            await keyboard_controller.start()

    async def _disable_keyboard_stream(self):
        """Disable keyboard stream and components at runtime"""
        # Stop keyboard controller
        keyboard_controller = self._components.get('keyboard_controller')
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
        clipboard_listener = self._components.get('clipboard_listener')
        if not clipboard_listener:
            clipboard_listener = ClipboardListener(
                event_bus=self.event_bus,
                stream_handler=clipboard_stream,
                command_stream=command_stream
            )
            if is_enabled and self._connected:
                await clipboard_listener.start()
            self._components['clipboard_listener'] = clipboard_listener
        elif is_enabled and self._connected and not clipboard_listener.is_alive():
            await clipboard_listener.start()

        # Clipboard Controller - handles incoming clipboard updates from server
        clipboard_controller = self._components.get('clipboard_controller')
        if not clipboard_controller and clipboard_listener:
            clipboard_controller = ClipboardController(
                event_bus=self.event_bus,
                clipboard=clipboard_listener.get_clipboard_context(),
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
        await self.event_bus.dispatch(
            event_type=EventType.CLIENT_ACTIVE,
            data={}
        )

        self._logger.info(f"Connected to server at {client.get_net_id()}")

    async def _on_disconnected(self, client: ClientObj):
        """Handle disconnection from server event"""
        self._connected = False

        # Dispatch event
        await self.event_bus.dispatch(
            event_type=EventType.CLIENT_INACTIVE,
            data={}
        )

        # Stop all stream handlers
        for stream_type, handler in list(self._stream_handlers.items()):
            try:
                await handler.stop()
            except Exception as e:
                self._logger.error(f"Error stopping stream handler {stream_type}: {e}")

        # Stop all components
        for component_name, component in list(self._components.items()):
            try:
                if hasattr(component, 'stop'):
                    if asyncio.iscoroutinefunction(component.stop):
                        await component.stop()
                    else:
                        component.stop()
            except Exception as e:
                self._logger.error(f"Error stopping component {component_name}: {e}")

        self._logger.info(f"Disconnected from server at {client.get_net_id()}")

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
        if not hasattr(self.client_config, 'streams_enabled'):
            return []
        return [k for k, v in self.client_config.streams_enabled.items() if v]

    def get_active_streams(self) -> list[int]:
        """Get list of currently active stream types"""
        return [st for st, handler in self._stream_handlers.items() if handler.is_active()]


# ==================== Example Usage ====================

async def main():
    """Example usage of Client API"""

    # Create configuration
    conn_config = ClientConnectionConfig(
        server_host="192.168.1.74",
        server_port=5555,
        auto_reconnect=True
    )

    # Create client
    client = Client(
        connection_config=conn_config,
        log_level=Logger.INFO
    )

    # Enable streams
    client.enable_stream(StreamType.MOUSE)
    client.enable_stream(StreamType.KEYBOARD)
    client.enable_stream(StreamType.CLIPBOARD)

    # Start client
    if not await client.start():
        print("Failed to start client")
        return

    print(f"Client started successfully")
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
        print("\nKeyboard interrupt received")
    finally:
        print("Stopping client...")
        await client.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nClient shutdown complete")

