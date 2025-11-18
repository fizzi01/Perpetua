from event import EventType
from model.ClientObj import ClientObj, ClientsManager
from event.EventBus import ThreadSafeEventBus

from command import CommandHandler

from network.connection.ClientConnectionService import ClientConnectionHandler
from network.data.MessageExchange import MessageExchange
from network.stream.ClientCustomStream import UnidirectionalStreamHandler, BidirectionalStreamHandler
from network.stream import StreamType

from input.mouse import ClientMouseController

from utils.logging import Logger
Logger(logging=True, stdout=print)

class ActiveClient:

    def __init__(self, server_ip: str, server_port: int):
        self.clients_manager = ClientsManager(client_mode=True)
        self.clients_manager.add_client(ClientObj(ip_address=server_ip, ssl=False))

        # Create EventBus
        self.event_bus = ThreadSafeEventBus()

        self.message_exchange = MessageExchange()

        # Create Stream Handlers
        self.command_stream_handler = BidirectionalStreamHandler(stream_type=StreamType.COMMAND,
                                                                 clients=self.clients_manager,
                                                                 event_bus=self.event_bus,
                                                                 handler_id="ClientCommandStreamHandler",
                                                                 instant=False) #False because we use async callbacks

        self.mouse_stream_handler = UnidirectionalStreamHandler(stream_type=StreamType.MOUSE,
                                                             clients=self.clients_manager,
                                                             event_bus=self.event_bus,
                                                             handler_id="ClientMouseStreamHandler",
                                                             sender=False, instant=False,
                                                                active_only=True) # Mouse data is received from server

        self.open_streams = [StreamType.MOUSE]

        self.client = ClientConnectionHandler(msg_exchange=self.message_exchange, host=server_ip, port=server_port,
                                              open_streams=self.open_streams,
                                              clients=self.clients_manager,
                                              wait=1,
                                              connected_callback=self.connected_callback,
                                              disconnected_callback=self.disconnected_callback)

        # Create Command Handler
        self.command_handler = CommandHandler(event_bus=self.event_bus, stream=self.command_stream_handler)

        # Create Mouse Controller
        self.mouse_controller = ClientMouseController(event_bus=self.event_bus, stream_handler=self.mouse_stream_handler,
                                                      command_stream=self.command_stream_handler)

    def connected_callback(self, client):
        self.event_bus.dispatch(event_type=EventType.CLIENT_ACTIVE, data={})

        self.mouse_stream_handler.start()
        self.command_stream_handler.start()

    def disconnected_callback(self, client):
        self.event_bus.dispatch(event_type=EventType.CLIENT_INACTIVE, data={})

        self.mouse_stream_handler.stop()
        self.command_stream_handler.stop()

    def start(self):
        self.client.start()
        self.mouse_controller.start()

    def stop(self):
        self.client.stop()
        self.mouse_stream_handler.stop()
        self.command_stream_handler.stop()
        self.mouse_controller.stop()

if __name__ == '__main__':
    active_client = ActiveClient(server_ip="", server_port=5555)
    active_client.start()

    print("Client started")
    try:
        while True:
            cmd = input("Type 'exit' to stop the client: ")
            if cmd.strip().lower() == "exit":
                break
    except KeyboardInterrupt:
        pass
    print("Stopping client...")
    active_client.stop()