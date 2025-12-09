"""
Unified Server API for PyContinuity
Provides a clean interface to configure and manage server components.
Supports runtime enable/disable of streams and listeners.
"""
import asyncio
from typing import Optional, Dict
from dataclasses import dataclass

from config import ApplicationConfig, ServerConfig
from model.ClientObj import ClientObj, ClientsManager
from event.EventBus import AsyncEventBus
from event import EventType
from network.connection.AsyncServerConnectionService import AsyncServerConnectionHandler
from network.stream.ServerCustomStream import (
    UnidirectionalStreamHandler,
    BidirectionalStreamHandler,
    BroadcastStreamHandler
)
from network.stream import StreamType, GenericStream
from command import CommandHandler
from input.cursor import CursorHandlerWorker
from input.mouse import ServerMouseListener, ServerMouseController
from input.keyboard import ServerKeyboardListener
from input.clipboard import ClipboardListener, ClipboardController
from utils.logging import Logger


@dataclass
class ServerConnectionConfig:
    """Server connection configuration"""
    host: str = "0.0.0.0"
    port: int = 5555
    heartbeat_interval: int = 1


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
        # Initialize configurations
        self.app_config = app_config or ApplicationConfig()
        self.server_config = server_config or ServerConfig(self.app_config)
        self.connection_config = connection_config or ServerConnectionConfig()

        # Set logging level
        Logger().set_level(log_level)
        self.logger = Logger()

        # Initialize core components
        self.clients_manager = ClientsManager()
        self.event_bus = AsyncEventBus()

        # Stream handlers registry
        self._stream_handlers: Dict[int, GenericStream] = {}

        # Components registry
        self._components = {}
        self._running = False

        # Connection handler
        self.connection_handler: Optional[AsyncServerConnectionHandler] = None

    # ==================== Client Management ====================

    def add_client(self, ip_address: str, screen_position: str = "top") -> ClientObj:
        """Add a client to the whitelist"""
        client = ClientObj(ip_address=ip_address, screen_position=screen_position)
        self.clients_manager.add_client(client)
        self.logger.info(f"Added client {ip_address} at position {screen_position}")
        return client

    async def remove_client(self, ip_address: str = None, screen_position: str = None) -> bool:
        """Remove a client from the whitelist"""
        client = self.clients_manager.get_client(ip_address=ip_address, screen_position=screen_position)
        if client:
            await self.connection_handler.force_disconnect_client(client)
            # Finally remove from whitelist
            self.clients_manager.remove_client(client)
            self.logger.info(f"Removed client {ip_address or screen_position}")
            return True
        return False

    def get_clients(self) -> list[ClientObj]:
        """Get all registered clients"""
        return self.clients_manager.get_clients()

    def get_client(self, ip_address: str = None, screen_position: str = None) -> Optional[ClientObj]:
        """Get a specific client"""
        return self.clients_manager.get_client(ip_address=ip_address, screen_position=screen_position)

    def is_clien_alive(self, ip_address: str) -> bool:
        """Check if a client is currently connected"""
        client = self.clients_manager.get_client(ip_address=ip_address)
        return client.is_connected if client else False

    def clear_clients(self):
        """Remove all clients from whitelist"""
        self.clients_manager.clients.clear()
        self.logger.info("Cleared all clients")

    # ==================== Stream Management ====================

    def enable_stream(self, stream_type: int):
        """Enable a specific stream type (applies before start or at runtime)"""
        self.server_config.enable_stream(stream_type)
        self.logger.info(f"Enabled stream: {stream_type}")

    def disable_stream(self, stream_type: int):
        """Disable a specific stream type (applies before start or at runtime)"""
        if StreamType.COMMAND == stream_type:
            self.logger.warning("Command stream is always enabled and cannot be disabled")
            return
        self.server_config.disable_stream(stream_type)
        self.logger.info(f"Disabled stream: {stream_type}")

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
            self.logger.warning(f"Stream {stream_type} already enabled")
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
                self.logger.error(f"Unknown stream type: {stream_type}")
                return False

            self.logger.info(f"Runtime enabled stream: {stream_type}")
            return True
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.disable_stream(stream_type)
            self.logger.error(f"Failed to enable {stream_type} stream: {e}")
            raise RuntimeError(f"Failed to enable {stream_type} stream: {e}")

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
                self.logger.error(f"Unknown stream type: {stream_type}")
                return False

            self.logger.info(f"Runtime disabled stream: {stream_type}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to disable {stream_type} stream: {e}")
            raise RuntimeError(f"Failed to disable {stream_type} stream: {e}")

    # ==================== Server Lifecycle ====================

    async def start(self) -> bool:
        """Start the server with enabled components"""
        if self._running:
            self.logger.warning("Server already running")
            return False

        self.logger.info("Starting Server...")

        # Initialize connection handler
        self.connection_handler = AsyncServerConnectionHandler(
            connected_callback=self._on_client_connected,
            disconnected_callback=self._on_client_disconnected,
            host=self.connection_config.host,
            port=self.connection_config.port,
            heartbeat_interval=self.connection_config.heartbeat_interval,
            whitelist=self.clients_manager
        )

        if not await self.connection_handler.start():
            self.logger.error("Failed to start connection handler")
            return False

        # Initialize and start enabled streams
        try:
            await self._initialize_streams()
        except Exception as e:
            self.logger.error(f"Failed to initialize streams: {e}")
            await self.connection_handler.stop()
            return False

        # Initialize and start enabled components
        try:
            await self._initialize_components()
        except Exception as e:
            self.logger.error(f"Failed to initialize components: {e}")
            await self.stop()
            return False

        self._running = True
        self.logger.info(f"Server started on {self.connection_config.host}:{self.connection_config.port}")
        return True

    async def stop(self):
        """Stop all server components"""
        if not self._running:
            self.logger.warning("Server not running")
            return

        self.logger.info("Stopping PyContinuity Server...")

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
                self.logger.error(f"Error stopping component {component_name}: {e}")

        # Stop all stream handlers
        for stream_type, handler in list(self._stream_handlers.items()):
            try:
                if hasattr(handler, 'stop'):
                    await handler.stop()
            except Exception as e:
                self.logger.error(f"Error stopping stream handler {stream_type}: {e}")

        self._components.clear()
        self._stream_handlers.clear()
        self._running = False
        self.logger.info("Server stopped")

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
        self._stream_handlers[StreamType.CLIPBOARD] = BroadcastStreamHandler(
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

        # Mouse components
        # if self.is_stream_enabled(StreamType.MOUSE):
        await self._enable_mouse_stream()

        # Keyboard components
        #if self.is_stream_enabled(StreamType.KEYBOARD):
        await self._enable_keyboard_stream()

        # Clipboard components
        #if self.is_stream_enabled(StreamType.CLIPBOARD):
        await self._enable_clipboard_stream()

    # ==================== Runtime Enable/Disable Methods ====================

    async def _enable_mouse_stream(self):
        """Enable mouse stream and components at runtime"""
        mouse_stream = self._stream_handlers.get(StreamType.MOUSE)

        # Create stream handler if not exists
        if not mouse_stream:
            mouse_stream = UnidirectionalStreamHandler(
                stream_type=StreamType.MOUSE,
                clients=self.clients_manager,
                event_bus=self.event_bus,
                handler_id="ServerMouseStreamHandler",
                sender=True
            )
            if not await mouse_stream.start():
                raise RuntimeError("Failed to start mouse stream handler")

            self._stream_handlers[StreamType.MOUSE] = mouse_stream
        elif not mouse_stream.is_active():
            if not await mouse_stream.start():
                raise RuntimeError("Failed to start mouse stream handler")

        # Create and start components if not exists
        command_stream = self._stream_handlers[StreamType.COMMAND]
        if 'cursor_handler' not in self._components:
            cursor_handler = CursorHandlerWorker(
                event_bus=self.event_bus,
                stream=mouse_stream,
                debug=False
            )
            if self.is_stream_enabled(StreamType.MOUSE): # We create it but don't start if not enabled
                if not cursor_handler.start():
                    if StreamType.MOUSE not in self._stream_handlers:
                        await mouse_stream.stop()
                    raise RuntimeError("Failed to start cursor handler")

            self._components['cursor_handler'] = cursor_handler
        elif not self._components['cursor_handler'].is_alive():
                if not self._components['cursor_handler'].start():
                    await self._disable_mouse_stream()
                    raise RuntimeError("Failed to start cursor handler")

        if 'mouse_controller' not in self._components:
            mouse_controller = ServerMouseController(event_bus=self.event_bus)
            self._components['mouse_controller'] = mouse_controller

        if 'mouse_listener' not in self._components:
            mouse_listener = ServerMouseListener(
                event_bus=self.event_bus,
                stream_handler=mouse_stream,
                command_stream=command_stream,
                filtering=False
            )
            if self.is_stream_enabled(StreamType.MOUSE): # We create it but don't start if not enabled
                if not mouse_listener.start():
                    await self._disable_mouse_stream()
                    raise RuntimeError("Failed to start mouse listener")

            self._components['mouse_listener'] = mouse_listener
        elif not self._components['mouse_listener'].is_alive():
            if not self._components['mouse_listener'].start():
                await self._disable_mouse_stream()
                raise RuntimeError("Failed to start mouse listener")


    async def _disable_mouse_stream(self):
        """Disable mouse stream and components at runtime"""
        # Stop components
        if 'mouse_listener' in self._components:
            self._components['mouse_listener'].stop()
            #del self._components['mouse_listener']

        # We can disable only once, so we stop it only with server stop
        #if 'cursor_handler' in self._components:
            #await self._components['cursor_handler'].stop()
            #del self._components['cursor_handler']

        #if 'mouse_controller' in self._components:
            #del self._components['mouse_controller']

        # Stop stream handler
        if StreamType.MOUSE in self._stream_handlers:
            await self._stream_handlers[StreamType.MOUSE].stop()
            #del self._stream_handlers[StreamType.MOUSE]

    async def _enable_keyboard_stream(self):
        """Enable keyboard stream and components at runtime"""
        keyboard_stream = self._stream_handlers.get(StreamType.KEYBOARD)

        # Create stream handler if not exists
        if not keyboard_stream:
            keyboard_stream = UnidirectionalStreamHandler(
                stream_type=StreamType.KEYBOARD,
                clients=self.clients_manager,
                event_bus=self.event_bus,
                handler_id="ServerKeyboardStreamHandler",
                sender=True
            )
            if not await keyboard_stream.start():
                raise RuntimeError("Failed to start keyboard stream handler")

            self._stream_handlers[StreamType.KEYBOARD] = keyboard_stream
        elif not keyboard_stream.is_active():
            if not await keyboard_stream.start():
                raise RuntimeError("Failed to start mouse stream handler")

        # Create and start components if not exists
        if 'keyboard_listener' not in self._components:
            command_stream = self._stream_handlers[StreamType.COMMAND]
            keyboard_listener = ServerKeyboardListener(
                event_bus=self.event_bus,
                stream_handler=keyboard_stream,
                command_stream=command_stream
            )
            if self.is_stream_enabled(StreamType.KEYBOARD):
                if not keyboard_listener.start():
                    if StreamType.KEYBOARD not in self._stream_handlers:
                        await keyboard_stream.stop()
                    raise RuntimeError("Failed to start keyboard listener")

            self._components['keyboard_listener'] = keyboard_listener
        elif not self._components['keyboard_listener'].is_alive():
            if not self._components['keyboard_listener'].start():
                await self._disable_keyboard_stream()
                raise RuntimeError("Failed to start keyboard listener")

    async def _disable_keyboard_stream(self):
        """Disable keyboard stream and components at runtime"""
        if 'keyboard_listener' in self._components:
            self._components['keyboard_listener'].stop()
            #del self._components['keyboard_listener']

        if StreamType.KEYBOARD in self._stream_handlers:
            await self._stream_handlers[StreamType.KEYBOARD].stop()
            #del self._stream_handlers[StreamType.KEYBOARD]

    async def _enable_clipboard_stream(self):
        """Enable clipboard stream and components at runtime"""
        clipboard_stream = self._stream_handlers.get(StreamType.CLIPBOARD)

        # Create stream handler if not exists
        if not clipboard_stream:
            clipboard_stream = BroadcastStreamHandler(
                stream_type=StreamType.CLIPBOARD,
                clients=self.clients_manager,
                event_bus=self.event_bus,
                handler_id="ServerClipboardStreamHandler"
            )
            if not await clipboard_stream.start():
                raise RuntimeError("Failed to start clipboard stream handler")

            self._stream_handlers[StreamType.CLIPBOARD] = clipboard_stream
        elif not clipboard_stream.is_active():
            if not await clipboard_stream.start():
                raise RuntimeError("Failed to start mouse stream handler")

        # Create and start components if not exists
        if 'clipboard_listener' not in self._components:
            command_stream = self._stream_handlers[StreamType.COMMAND]
            clipboard_listener = ClipboardListener(
                event_bus=self.event_bus,
                stream_handler=clipboard_stream,
                command_stream=command_stream
            )
            if self.is_stream_enabled(StreamType.CLIPBOARD):
                if not await clipboard_listener.start():
                    if StreamType.CLIPBOARD not in self._stream_handlers:
                        await clipboard_stream.stop()
                    raise RuntimeError("Failed to start clipboard listener")

            self._components['clipboard_listener'] = clipboard_listener
        elif not self._components['clipboard_listener'].is_alive():
            if not await self._components['clipboard_listener'].start():
                await self._disable_clipboard_stream()
                raise RuntimeError("Failed to start clipboard listener")

        if 'clipboard_controller' not in self._components:
            clipboard_controller = ClipboardController(
                event_bus=self.event_bus,
                clipboard=self._components['clipboard_listener'].get_clipboard_context(),
                stream_handler=clipboard_stream
            )

            self._components['clipboard_controller'] = clipboard_controller

    async def _disable_clipboard_stream(self):
        """Disable clipboard stream and components at runtime"""
        if 'clipboard_listener' in self._components:
            await self._components['clipboard_listener'].stop()
            #del self._components['clipboard_listener']

        #if 'clipboard_controller' in self._components:
            #del self._components['clipboard_controller']

        if StreamType.CLIPBOARD in self._stream_handlers:
            await self._stream_handlers[StreamType.CLIPBOARD].stop()
            #del self._stream_handlers[StreamType.CLIPBOARD]

    # ==================== Event Callbacks ====================

    async def _on_client_connected(self, client: ClientObj, streams: list[int]):
        """Handle client connection event"""
        await self.event_bus.dispatch(
            event_type=EventType.CLIENT_CONNECTED,
            data={"client_screen": client.screen_position, "streams": streams}
        )
        self.logger.info(f"Client {client.ip_address} connected at position {client.screen_position}")

    async def _on_client_disconnected(self, client: ClientObj, streams: list[int]):
        """Handle client disconnection event"""
        await self.event_bus.dispatch(
            event_type=EventType.CLIENT_DISCONNECTED,
            data={"client_screen": client.screen_position, "streams": streams}
        )
        self.logger.info(f"Client {client.ip_address} disconnected from position {client.screen_position}")

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
    server.add_client("192.168.1.74", screen_position="top")

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

