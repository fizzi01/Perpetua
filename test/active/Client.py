"""
Complete client logic suite for active tests.
Fully async implementation using AsyncEventBus and async connection handlers.
"""
import asyncio
from event import EventType
from model.client import ClientObj, ClientsManager
from event.bus import AsyncEventBus  # Changed to AsyncEventBus

from command import CommandHandler

from network.connection.client import ConnectionHandler  # Changed to async
from network.stream.client import UnidirectionalStreamHandler, BidirectionalStreamHandler
from network.stream import StreamType

from input.mouse import ClientMouseController
from input.keyboard import ClientKeyboardController
from input.clipboard import ClipboardController, ClipboardListener

from utils.logging import Logger

Logger().set_level(Logger.DEBUG)  # Set log level to reduce output during tests

class ActiveClient:

    def __init__(self, server_ip: str, server_port: int):
        self.clients_manager = ClientsManager(client_mode=True)
        self.clients_manager.add_client(ClientObj(ip_address=server_ip, ssl=False))

        # Create AsyncEventBus
        self.event_bus = AsyncEventBus()

        # Create Stream Handlers (no more instant parameter - always async)
        self.command_stream_handler = BidirectionalStreamHandler(
            stream_type=StreamType.COMMAND,
            clients=self.clients_manager,
            event_bus=self.event_bus,
            handler_id="ClientCommandStreamHandler"
        )

        self.mouse_stream_handler = UnidirectionalStreamHandler(
            stream_type=StreamType.MOUSE,
            clients=self.clients_manager,
            event_bus=self.event_bus,
            handler_id="ClientMouseStreamHandler",
            sender=False,  # Mouse data is received from server
            active_only=True
        )

        self.keyboard_stream_handler = UnidirectionalStreamHandler(
            stream_type=StreamType.KEYBOARD,
            clients=self.clients_manager,
            event_bus=self.event_bus,
            handler_id="ClientKeyboardStreamHandler",
            sender=False,  # Keyboard data is received from server
            active_only=True
        )

        self.clipboard_stream_handler = BidirectionalStreamHandler(
            stream_type=StreamType.CLIPBOARD,
            clients=self.clients_manager,
            event_bus=self.event_bus,
            handler_id="ClientClipboardStreamHandler"
        )

        self.open_streams = [StreamType.MOUSE, StreamType.COMMAND, StreamType.KEYBOARD, StreamType.CLIPBOARD]

        # Create Async Client Connection Handler
        self.client = ConnectionHandler(
            host=server_ip,
            port=server_port,
            heartbeat_interval=1,
            clients=self.clients_manager,
            open_streams=self.open_streams,
            connected_callback=self.connected_callback,
            disconnected_callback=self.disconnected_callback
        )

        # Create Command Handler
        self.command_handler = CommandHandler(
            event_bus=self.event_bus,
            stream=self.command_stream_handler
        )

        # Create Mouse Controller
        self.mouse_controller = ClientMouseController(
            event_bus=self.event_bus,
            stream_handler=self.mouse_stream_handler,
            command_stream=self.command_stream_handler
        )

        # Create Keyboard Controller
        self.keyboard_controller = ClientKeyboardController(
            event_bus=self.event_bus,
            stream_handler=self.keyboard_stream_handler,
            command_stream=self.command_stream_handler
        )

        # Create Clipboard Listener and Controller
        self.clipboard_listener = ClipboardListener(
            event_bus=self.event_bus,
            stream_handler=self.clipboard_stream_handler,
            command_stream=self.command_stream_handler
        )
        self.clipboard_controller = ClipboardController(
            event_bus=self.event_bus,
            clipboard=self.clipboard_listener.get_clipboard_context(),
            stream_handler=self.clipboard_stream_handler
        )


    async def connected_callback(self, client):
        """Async callback for connection"""
        await self.event_bus.dispatch(
            event_type=EventType.CLIENT_ACTIVE,
            data={}
        )

        await self.mouse_stream_handler.start()
        await self.command_stream_handler.start()
        await self.keyboard_stream_handler.start()
        await self.clipboard_stream_handler.start()

    async def disconnected_callback(self, client):
        """Async callback for disconnection"""
        await self.event_bus.dispatch(
            event_type=EventType.CLIENT_INACTIVE,
            data={}
        )

        await self.mouse_stream_handler.stop()
        await self.command_stream_handler.stop()
        await self.keyboard_stream_handler.stop()
        await self.clipboard_stream_handler.stop()

    async def start(self):
        """Start client asynchronously"""
        # Connect to server
        await self.client.start()

        # Start controller
        # We can start on start since controllers will work only when receiving events
        await self.mouse_controller.start()
        await self.keyboard_controller.start()
        await self.clipboard_controller.start()
        await self.clipboard_listener.start()

    async def stop(self):
        """Stop client asynchronously"""
        # Stop controller
        await self.mouse_controller.stop()
        await self.keyboard_controller.stop()
        await self.clipboard_controller.stop()
        await self.clipboard_listener.stop()

        # Stop stream handlers
        await self.mouse_stream_handler.stop()
        await self.command_stream_handler.stop()
        await self.keyboard_stream_handler.stop()
        await self.clipboard_stream_handler.stop()

        # Disconnect from server
        await self.client.stop()


async def main():
    """Async main function"""
    active_client = ActiveClient(server_ip="192.168.1.74", server_port=5555)
    await active_client.start()

    print("Client started")
    print("Press Ctrl+C to stop")

    try:
        # Run indefinitely until interrupted
        while True:
            await asyncio.sleep(0)
    except KeyboardInterrupt:
        print("\nKeyboard interrupt received")

    print("Stopping client...")
    await active_client.stop()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nClient shutdown complete")
