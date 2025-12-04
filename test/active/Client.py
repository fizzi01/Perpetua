"""
Complete client logic suite for active tests.
Fully async implementation using AsyncEventBus and async connection handlers.
"""
import asyncio
from event import EventType
from model.ClientObj import ClientObj, ClientsManager
from event.EventBus import AsyncEventBus  # Changed to AsyncEventBus

from command import CommandHandler

from network.connection.AsyncClientConnectionService import AsyncClientConnectionHandler  # Changed to async
from network.stream.ClientCustomStream import UnidirectionalStreamHandler, BidirectionalStreamHandler
from network.stream import StreamType

from input.mouse import ClientMouseController

from utils.logging import Logger
Logger(logging=True, stdout=print)

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

        self.open_streams = [StreamType.MOUSE]

        # Create Async Client Connection Handler
        self.client = AsyncClientConnectionHandler(
            host=server_ip,
            port=server_port,
            heartbeat_interval=30,
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

    async def connected_callback(self, client):
        """Async callback for connection"""
        await self.event_bus.dispatch(
            event_type=EventType.CLIENT_ACTIVE,
            data={}
        )

        await self.mouse_stream_handler.start()
        await self.command_stream_handler.start()

    async def disconnected_callback(self, client):
        """Async callback for disconnection"""
        await self.event_bus.dispatch(
            event_type=EventType.CLIENT_INACTIVE,
            data={}
        )

        await self.mouse_stream_handler.stop()
        await self.command_stream_handler.stop()

    async def start(self):
        """Start client asynchronously"""
        # Connect to server
        await self.client.start()

        # Start mouse controller
        await self.mouse_controller.start()

    async def stop(self):
        """Stop client asynchronously"""
        # Stop mouse controller
        await self.mouse_controller.stop()

        # Stop stream handlers
        await self.mouse_stream_handler.stop()
        await self.command_stream_handler.stop()

        # Disconnect from server
        await self.client.stop()


async def main():
    """Async main function"""
    active_client = ActiveClient(server_ip="192.168.1.62", server_port=5555)
    await active_client.start()

    print("Client started")
    print("Press Ctrl+C to stop")

    try:
        # Run indefinitely until interrupted
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        print("\nKeyboard interrupt received")

    print("Stopping client...")
    await active_client.stop()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nClient shutdown complete")
