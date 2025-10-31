from queue import Empty
from typing import Optional

from utils.logging.logger import Logger
from network.connection.GeneralSocket import BaseSocket
from network.stream.GenericStream import StreamHandler
from network.data.MessageExchange import MessageExchange, MessageExchangeConfig
from model.ClientObj import ClientsManager, ClientObj

from event.EventBus import EventBus
from event.Event import EventType

class UnidirectionalStreamHandler(StreamHandler):
    """
    A custom stream handler for managing mouse input streams. (Unidirectional: Server -> Client)
    """

    def __init__(self, stream_type: int, clients: ClientsManager, event_bus: EventBus, handler_id: Optional[str] = None, source: str = "server"):
        super().__init__(stream_type=stream_type, clients=clients, event_bus=event_bus, bidirectional=False)

        self._active_client = None
        self.handler_id = handler_id if handler_id else f"UnidirectionalStreamHandler_{stream_type}"
        self.source = source

        # Create a MessageExchange object
        self.msg_exchange = MessageExchange(
            conf = MessageExchangeConfig()
        )

        self.logger = Logger.get_instance()

        event_bus.subscribe(event_type=EventType.ACTIVE_SCREEN_CHANGED,  callback=self._on_active_screen_changed)


    def _on_active_screen_changed(self, data: dict):
        """
        Event handler for when the active screen changes.
        """

        # Get current active screen from event data
        active_screen = data.get("active_screen")

        # Find corresponding client
        self._active_client: Optional[ClientObj] = self.clients.get_client(screen_position=active_screen)

        # Set message exchange active client
        if self._active_client:
            # Try to get corresponding stream socket
            cl_stram_socket = self._active_client.conn_socket
            if isinstance(cl_stram_socket, BaseSocket):
                self.msg_exchange.set_transport(send_callback=cl_stram_socket.get_stream(self.stream_type).send,
                                                receive_callback=cl_stram_socket.get_stream(self.stream_type).recv)
            else:
                self.logger.log(f"{self.handler_id}: No valid stream for active client {self._active_client.screen_position}", Logger.WARNING)
                self.msg_exchange.set_transport(send_callback=None, receive_callback=None)

            # Empty the send queue
            with self._send_queue.mutex:
                self._send_queue.queue.clear()
        else:
            self.msg_exchange.set_transport(send_callback=None, receive_callback=None)


    def _core_sender(self):
        """
        Core loop for handling mouse input stream.
        """

        while self._active:
            if self._active_client and self._active_client.is_connected:
                try:
                    # Process sending queued mouse data
                    while not self._send_queue.empty():
                        data = self._send_queue.get(timeout=0.001) # It should be a dictionary from Event.to_dict()
                        self.msg_exchange.send_stream_type_message(stream_type=self.stream_type,**data,source=self.source, target=self._active_client.screen_position)

                except Empty:
                    continue
                except Exception as e:
                    self.logger.log(f"Error in {self.handler_id} core loop: {e}", Logger.ERROR)



class BidirectionalStreamHandler(StreamHandler):
    """
    A custom stream handler for managing bidirectional streams. Server <-> Client
    """

    def __init__(self, stream_type: int, clients: ClientsManager, event_bus: EventBus, handler_id: Optional[str] = None, source: str = "server", instant: bool = True):
        """

        Attributes:
            instant (bool): If True, the stream receive data instantly. (if false, it receives with a specified callback for different message types)
        """
        super().__init__(stream_type=stream_type, clients=clients, event_bus=event_bus, bidirectional=True)

        self._active_client = None
        self.handler_id = handler_id if handler_id else f"UnidirectionalStreamHandler_{stream_type}"
        self.source = source
        self.instant = instant

        # Create a MessageExchange object
        self.msg_exchange = MessageExchange(
            conf = MessageExchangeConfig()
        )

        self.logger = Logger.get_instance()

        event_bus.subscribe(event_type=EventType.ACTIVE_SCREEN_CHANGED,  callback=self._on_active_screen_changed)

    def register_receive_callback(self, receive_callback, message_type: str):
        """
        Register a callback function for receiving messages of a specific type.
        """
        self.msg_exchange.register_handler(message_type, receive_callback)

    def _on_active_screen_changed(self, data: dict):
        """
        Event handler for when the active screen changes.
        """

        # Get current active screen from event data
        active_screen = data.get("active_screen")

        # Find corresponding client
        self._active_client: Optional[ClientObj] = self.clients.get_client(screen_position=active_screen)

        # Set message exchange active client
        if self._active_client:
            # Try to get corresponding stream socket
            cl_stram_socket = self._active_client.conn_socket
            if isinstance(cl_stram_socket, BaseSocket):
                self.msg_exchange.set_transport(send_callback=cl_stram_socket.get_stream(self.stream_type).send,
                                                receive_callback=cl_stram_socket.get_stream(self.stream_type).recv)
            else:
                self.logger.log(
                    f"{self.handler_id}: No valid stream for active client {self._active_client.screen_position}",
                    Logger.WARNING)
                self.msg_exchange.set_transport(send_callback=None, receive_callback=None)

            # Empty the send queue
            with self._send_queue.mutex:
                self._send_queue.queue.clear()
        else:
            self.msg_exchange.set_transport(send_callback=None, receive_callback=None)

    def _core_sender(self):
        """
        Core loop for handling mouse input stream.
        """

        while self._active:
            if self._active_client and self._active_client.is_connected:
                try:
                    # Process sending queued mouse data
                    while not self._send_queue.empty():
                        data = self._send_queue.get(timeout=0.001) # It should be a dictionary from Event.to_dict()
                        self.msg_exchange.send_stream_type_message(stream_type=self.stream_type,**data,source=self.source, target=self._active_client.screen_position)

                except Empty:
                    continue
                except Exception as e:
                    self.logger.log(f"Error in {self.handler_id} core loop: {e}", Logger.ERROR)

    def _core_receiver(self):
        """
        Core loop for handling receiving data from the stream.
        """

        while self._active:
            if self._active_client and self._active_client.is_connected:
                try:
                    # Process incoming messages
                    msg = self.msg_exchange.receive_message(self.instant)
                    if msg:
                        self._recv_queue.put(msg)

                except Exception as e:
                    self.logger.log(f"Error in {self.handler_id} core receiver loop: {e}", Logger.ERROR)

