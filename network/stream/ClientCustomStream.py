from queue import Empty
from time import sleep
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
    A custom stream handler for managing connecton streams. (Unidirectional: Client -> Server)
    """

    def __init__(self, stream_type: int, clients: ClientsManager, event_bus: EventBus, handler_id: Optional[str] = None,
                 sender: bool = True, instant: bool = True, active_only: bool = False):
        super().__init__(stream_type=stream_type, clients=clients, event_bus=event_bus, bidirectional=False,
                         sender=sender)

        self._is_active = False # Track if current client is active

        self.handler_id = handler_id if handler_id else f"UnidirectionalStreamHandler_{stream_type}"
        self.instant = instant
        self._active_only = active_only

        # Create a MessageExchange object
        self.msg_exchange = MessageExchange(
            conf=MessageExchangeConfig()
        )

        # Get main client
        # If client manager is correctly initialized, it should have only one main client
        self._main_client: Optional[ClientObj] = self.clients.get_client()

        if not self._main_client:
            raise ValueError(f"No main client found in ClientsManager for {self.handler_id}")

        # Set message exchange transport source
        cl_stram_socket = self._main_client.conn_socket
        if isinstance(cl_stram_socket, BaseSocket):
            self.msg_exchange.set_transport(send_callback=cl_stram_socket.get_stream(self.stream_type).send,
                                            receive_callback=cl_stram_socket.get_stream(self.stream_type).recv)
        else:
            raise ValueError(f"Invalid connection socket for main client in {self.handler_id}")

        self.logger = Logger.get_instance()

        event_bus.subscribe(event_type=EventType.CLIENT_ACTIVE, callback=self._on_client_active)
        event_bus.subscribe(event_type=EventType.CLIENT_INACTIVE, callback=self._on_client_inactive)

    def _on_client_active(self, data: dict):
        """
        Event handler for when a client becomes active.
        """
        with self._rlock, self._slock: # TODO: Check if both locks are necessary
            self._is_active = True

    def _on_client_inactive(self, data: dict):
        """
        Event handler for when a client becomes inactive.
        """
        with self._rlock, self._slock: # TODO: Check if both locks are necessary
            self._is_active = False

    def register_receive_callback(self, receive_callback, message_type: str):
        """
        Register a callback function for receiving messages of a specific type.
        """
        self.msg_exchange.register_handler(message_type, receive_callback)


    def _core_sender(self):
        """
        Core sender loop for sending messages to the server
        """
        while self._active:
            with self._slock:
                
                if self._active_only and not self._is_active:
                    sleep(self._waiting_time)
                    continue
                    
                try:
                    while not self._send_queue.empty():
                        data = self._send_queue.get(timeout=self._waiting_time)  # It should be a dictionary from Event.to_dict()
                        self.msg_exchange.send_stream_type_message(stream_type=self.stream_type, **data,
                                                                   source=self._main_client.screen_position,
                                                                   target="server")
                except Empty:
                    sleep(self._waiting_time)
                    continue
                except Exception as e:
                    self.logger.log(f"Error in {self.handler_id} core loop: {e}", Logger.ERROR)
                    sleep(self._waiting_time)

    def _core_receiver(self):
        """
        Core receiver loop for handling incoming messages from the server
        """
        while self._active:
            with self._rlock:
                try:
                    message = self.msg_exchange.receive_message(self.instant)
                    if message:
                        self._recv_queue.put(message)
                except Empty:
                    continue
                except Exception as e:
                    self.logger.log(f"Error in {self.handler_id} core receiver loop: {e}", Logger.ERROR)



class BidirectionalStreamHandler(StreamHandler):
    """
    A custom stream handler for managing bidirectional streams. Client <-> Server
    """

    def __init__(self, stream_type: int, clients: ClientsManager, event_bus: EventBus, handler_id: Optional[str] = None,
                 instant: bool = True, active_only: bool = False):
        super().__init__(stream_type=stream_type, clients=clients, event_bus=event_bus, bidirectional=True)

        self._is_active = False # Track if current client is active

        self.handler_id = handler_id if handler_id else f"BidirectionalStreamHandler_{stream_type}"
        self.instant = instant
        self._active_only = active_only

        # Create a MessageExchange object
        self.msg_exchange = MessageExchange(
            conf=MessageExchangeConfig()
        )

        # Get main client
        # If client manager is correctly initialized, it should have only one main client
        self._main_client: Optional[ClientObj] = self.clients.get_client()

        if not self._main_client:
            raise ValueError(f"No main client found in ClientsManager for {self.handler_id}")

        # Set message exchange transport source
        cl_stram_socket = self._main_client.conn_socket
        if isinstance(cl_stram_socket, BaseSocket):
            self.msg_exchange.set_transport(send_callback=cl_stram_socket.get_stream(self.stream_type).send,
                                            receive_callback=cl_stram_socket.get_stream(self.stream_type).recv)
        else:
            raise ValueError(f"Invalid connection socket for main client in {self.handler_id}")

        self.logger = Logger.get_instance()

        event_bus.subscribe(event_type=EventType.CLIENT_ACTIVE, callback=self._on_client_active)
        event_bus.subscribe(event_type=EventType.CLIENT_INACTIVE, callback=self._on_client_inactive)

    def _on_client_active(self, data: dict):
        """
        Event handler for when a client becomes active.
        """
        with self._rlock, self._slock:
            self._is_active = True

    def _on_client_inactive(self, data: dict):
        """
        Event handler for when a client becomes inactive.
        """
        with self._rlock, self._slock:
            self._is_active = False

    def register_receive_callback(self, receive_callback, message_type: str):
        """
        Register a callback function for receiving messages of a specific type.
        """
        self.msg_exchange.register_handler(message_type, receive_callback)


    def _core_sender(self):
        """
        Core sender loop for sending messages to the server
        """
        while self._active:
            with self._slock:
                if self._active_only and not self._is_active:
                    sleep(self._waiting_time)
                    continue
                    
                try:
                    while not self._send_queue.empty():
                        data = self._send_queue.get(timeout=self._waiting_time)  # It should be a dictionary from Event.to_dict()
                        self.msg_exchange.send_stream_type_message(stream_type=self.stream_type, **data,
                                                                   source=self._main_client.screen_position,
                                                                   target="server")
                except Empty:
                    sleep(self._waiting_time)
                    continue
                except Exception as e:
                    self.logger.log(f"Error in {self.handler_id} core loop: {e}", Logger.ERROR)
                    sleep(self._waiting_time)

    def _core_receiver(self):
        """
        Core receiver loop for handling incoming messages from the server
        """
        while self._active:
            with self._rlock:
                try:
                    message = self.msg_exchange.receive_message(self.instant)
                    if message:
                        self._recv_queue.put(message)
                except Empty:
                    continue
                except Exception as e:
                    self.logger.log(f"Error in {self.handler_id} core receiver loop: {e}", Logger.ERROR)
