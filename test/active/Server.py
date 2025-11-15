"""
Complete server logic suite for active tests.
"""
from event import EventType
from network.data.MessageExchange import MessageExchange
# ServerConnectionService needs a Clientmanager with at least a ClienObj configured to connect to.
# We need also an EventBus to handle events and the first stream handler (command stream).
# (We need just one per stream cause the handler manages multiple clients).

# We then need to create in order: 1. CursorHandlerWorker 2. ServerMouseListener and Controller 3. CommandHandler



from model.ClientObj import ClientObj, ClientsManager
from event.EventBus import ThreadSafeEventBus

from network.connection.ServerConnectionServices import ServerConnectionHandler
from network.stream.ServerCustomStream import UnidirectionalStreamHandler, BidirectionalStreamHandler
from network.stream import StreamType

from command import CommandHandler

from input.cursor import CursorHandlerWorker
from input.mouse import ServerMouseListener, ServerMouseController

from utils.logging import Logger
Logger(logging=True, stdout=print)

class ActiveServer:
    def __init__(self, host: str, port: int, client_address: str):
        # Create ClientObj and ClientsManager
        client = ClientObj(ip_address=client_address, screen_position="top")
        self.clients_manager = ClientsManager().add_client(client)

        # Create EventBus
        self.event_bus = ThreadSafeEventBus()

        # Create Stream Handlers
        self.command_stream_handler = BidirectionalStreamHandler(stream_type=StreamType.COMMAND,
                                                                 clients=self.clients_manager,
                                                                 event_bus=self.event_bus,
                                                                 handler_id="ServerCommandStreamHandler",
                                                                 instant=False) #False because we use async callbacks

        self.mouse_stream_handler = UnidirectionalStreamHandler(stream_type=StreamType.MOUSE,
                                                             clients=self.clients_manager,
                                                             event_bus=self.event_bus,
                                                             handler_id="ServerMouseStreamHandler",
                                                             sender=True, instant=False) # Mouse data is sent from server to client

        # Create Cursor Handler Worker
        self.cursor_handler_worker = CursorHandlerWorker(event_bus=self.event_bus,
                                                         stream=self.mouse_stream_handler, debug=True)

        self.message_exchange = MessageExchange()

        # Create Connection Handler
        self.connection_handler = ServerConnectionHandler(
            msg_exchange=self.message_exchange,
            host=host,
            port=port,
            whitelist=self.clients_manager,
            connected_callback=self.on_client_connected,
            disconnected_callback=self.on_client_disconnected,
        )

        # Create Command Handler
        self.command_handler = CommandHandler(event_bus=self.event_bus, stream=self.command_stream_handler)

        # Create Mouse Listener and Controller
        self.mouse_controller = ServerMouseController(event_bus=self.event_bus)
        self.mouse_listener = ServerMouseListener(event_bus=self.event_bus, stream_handler=self.mouse_stream_handler,
                                                  command_stream=self.command_stream_handler, filtering=False)

    def on_client_connected(self, client: ClientObj):
        client_pos = client.screen_position
        self.event_bus.dispatch(event_type=EventType.CLIENT_CONNECTED, data={"client_screen": client_pos})

    def on_client_disconnected(self, client: ClientObj):
        client_pos = client.screen_position
        self.event_bus.dispatch(event_type=EventType.CLIENT_DISCONNECTED, data={"client_screen": client_pos})

    def start(self):
        # Start all components
        self.connection_handler.start()
        self.command_stream_handler.start()
        self.mouse_stream_handler.start()
        self.cursor_handler_worker.start()
        self.mouse_listener.start()

    def stop(self):
        # Stop all components
        self.mouse_listener.stop()
        self.cursor_handler_worker.stop()
        self.mouse_stream_handler.stop()
        self.command_stream_handler.stop()
        self.connection_handler.stop()

if __name__ == "__main__":
    server = ActiveServer(host="192.168.1.62", port=5555, client_address="192.168.1.74")
    server.start()

    print("Server started")

    # Writing exit will stop the server
    try:
        while True:
            cmd = input("Type 'exit' to stop the server: ")
            if cmd.strip().lower() == "exit":
                break
    except KeyboardInterrupt:
        pass
    print("Stopping server...")

    server.stop()

