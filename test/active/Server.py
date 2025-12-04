"""
Complete server logic suite for active tests.
Fully async implementation using AsyncEventBus and async connection handlers.
"""
import asyncio
from event import EventType
from network.data.MessageExchange import MessageExchange
# ServerConnectionService needs a Clientmanager with at least a ClienObj configured to connect to.
# We need also an EventBus to handle events and the first stream handler (command stream).
# (We need just one per stream cause the handler manages multiple clients).

# We then need to create in order: 1. CursorHandlerWorker 2. ServerMouseListener and Controller 3. CommandHandler

from model.ClientObj import ClientObj, ClientsManager
from event.EventBus import AsyncEventBus  # Changed to AsyncEventBus

from network.connection.AsyncServerConnectionService import AsyncServerConnectionHandler  # Changed to async
from network.stream.ServerCustomStream import UnidirectionalStreamHandler, BidirectionalStreamHandler
from network.stream import StreamType

from command import CommandHandler

from input.cursor import CursorHandlerWorker
from input.mouse import ServerMouseListener, ServerMouseController

from utils.logging import Logger

Logger().set_level(Logger.DEBUG)  # Set log level to reduce output during tests

class ActiveServer:
    def __init__(self, host: str, port: int, client_address: str):
        # Create ClientObj and ClientsManager
        client = ClientObj(ip_address=client_address, screen_position="top")
        self.clients_manager = ClientsManager()
        self.clients_manager.add_client(client)

        # Create AsyncEventBus
        self.event_bus = AsyncEventBus()

        # Create Stream Handlers (no more instant parameter - always async)
        self.command_stream_handler = BidirectionalStreamHandler(
            stream_type=StreamType.COMMAND,
            clients=self.clients_manager,
            event_bus=self.event_bus,
            handler_id="ServerCommandStreamHandler"
        )

        self.mouse_stream_handler = UnidirectionalStreamHandler(
            stream_type=StreamType.MOUSE,
            clients=self.clients_manager,
            event_bus=self.event_bus,
            handler_id="ServerMouseStreamHandler",
            sender=True  # Mouse data is sent from server to client
        )

        # Create Cursor Handler Worker
        self.cursor_handler_worker = CursorHandlerWorker(
            event_bus=self.event_bus,
            stream=self.mouse_stream_handler,
            debug=True
        )

        # Create Async Connection Handler
        self.connection_handler = AsyncServerConnectionHandler(
            connected_callback=self.on_client_connected,
            disconnected_callback=self.on_client_disconnected,
            host=host,
            port=port,
            heartbeat_interval=1,
            whitelist=self.clients_manager
        )

        # Create Command Handler
        self.command_handler = CommandHandler(
            event_bus=self.event_bus,
            stream=self.command_stream_handler
        )

        # Create Mouse Listener and Controller
        self.mouse_controller = ServerMouseController(event_bus=self.event_bus)
        self.mouse_listener = ServerMouseListener(
            event_bus=self.event_bus,
            stream_handler=self.mouse_stream_handler,
            command_stream=self.command_stream_handler,
            filtering=False
        )

    async def on_client_connected(self, client: ClientObj):
        """Async callback for client connection"""
        client_pos = client.screen_position
        await self.event_bus.dispatch(
            event_type=EventType.CLIENT_CONNECTED,
            data={"client_screen": client_pos}
        )

    async def on_client_disconnected(self, client: ClientObj):
        """Async callback for client disconnection"""
        client_pos = client.screen_position
        await self.event_bus.dispatch(
            event_type=EventType.CLIENT_DISCONNECTED,
            data={"client_screen": client_pos}
        )

    async def start(self) -> bool:
        """Start all components asynchronously"""
        # Start async connection handler
        if not await self.connection_handler.start():
            return False

        # Start stream handlers
        if not await self.command_stream_handler.start():
            await self.stop()
            return False

        if not await self.mouse_stream_handler.start():
            await self.stop()
            return False

        # Start cursor handler worker (sync method)
        if not self.cursor_handler_worker.start():
            await self.stop()
            return False

        # Start mouse listener (sync method - pynput thread)
        if not self.mouse_listener.start():
            await self.stop()
            return False

        return True

    async def stop(self):
        """Stop all components asynchronously"""
        # Stop connection handler
        await self.connection_handler.stop()
        # Stop mouse listener
        self.mouse_listener.stop()
        # Stop cursor handler
        await self.cursor_handler_worker.stop()
        # Stop stream handlers
        await self.mouse_stream_handler.stop()
        await self.command_stream_handler.stop()



async def main():
    """Async main function"""
    server = ActiveServer(host="192.168.1.62", port=5555, client_address="192.168.1.74")
    if not await server.start():
        print("Failed to start server")
        return

    print("Server started")
    print("Type 'exit' in another terminal or press Ctrl+C to stop")

    # Keep server running
    try:
        # Run indefinitely until interrupted
        while True:
            await asyncio.sleep(0)
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

