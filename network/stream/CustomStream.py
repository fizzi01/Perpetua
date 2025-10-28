from queue import Empty
from typing import Optional

from utils.logging.logger import Logger
from network.connection.GeneralSocket import BaseSocket
from network.stream.GenericStream import StreamHandler
from network.data.MessageExchange import MessageExchange, MessageExchangeConfig
from model.ClientObj import ClientsManager, ClientObj

from event.EventBus import EventBus
from event.Event import EventType

class MouseStreamHandler(StreamHandler):
    """
    A custom stream handler for managing mouse input streams.
    """

    def __init__(self, stream_type: int, clients: ClientsManager, event_bus: EventBus):
        super().__init__(stream_type=stream_type, clients=clients, event_bus=event_bus)

        self._active_client = None

        # Create MessageExchange object
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
                self.logger.log(f"MouseStreamHandler: No valid stream for active client {self._active_client.screen_position}", Logger.WARNING)
                self.msg_exchange.set_transport(send_callback=None, receive_callback=None)

            # Empty the send queue
            with self._send_queue.mutex:
                self._send_queue.queue.clear()
        else:
            self.msg_exchange.set_transport(send_callback=None, receive_callback=None)


    def _core(self):
        """
        Core loop for handling mouse input stream.
        """

        while self._active:
            if self._active_client and self._active_client.is_connected:
                try:
                    # Process sending queued mouse data
                    while not self._send_queue.empty():
                        mouse_data = self._send_queue.get(timeout=0.001) # It should be a dictionary from MouseEvent.to_dict()
                        self.msg_exchange.send_mouse_data(**mouse_data, target=self._active_client.screen_position)

                except Empty:
                    continue
                except Exception as e:
                    self.logger.log(f"Error in MouseStreamHandler core loop: {e}", Logger.ERROR)