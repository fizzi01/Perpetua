from queue import Queue
from threading import Thread, Lock
from typing import Any

from model.ClientObj import ClientsManager
from event.EventBus import EventBus
from utils.logging import Logger


class StreamHandler:
    """
    A generic stream handler class for managing network streams.
    """

    def __init__(self, stream_type: int, clients: ClientsManager, event_bus: EventBus,
                 bidirectional: bool = False, sender: bool = True):
        """
        Attributes:
            stream_type (int): The type of stream (e.g., mouse, keyboard, command).
            clients (ClientsManager): Manager for connected clients.
            event_bus (EventBus): Event bus for handling events.
            bidirectional (bool): If True, the stream supports both sending and receiving.
            sender (bool): If True, the stream sends data. (if false, it only receives)
        """
        self.stream_type = stream_type
        self.clients = clients
        self.event_bus = event_bus
        self._send_queue = Queue()
        self._recv_queue = Queue()
        self._active = False
        self._sender_thread = None
        self._receiver_thread = None

        self._slock = Lock()
        self._rlock = Lock()

        self._bidirectional = bidirectional
        self._sender = sender

        self._waiting_time = 0.001  # Time to wait in loops to prevent busy waiting

        self.logger = Logger.get_instance()

    def register_receive_callback(self, receive_callback, message_type: str):
        """
        Register a callback function for receiving messages of a specific type.
        """
        raise NotImplementedError

    def start(self):
        """
        Starts the stream handler.
        """
        self._active = True
        if self._sender:
            self._sender_thread = Thread(target=self._core_sender, daemon=True)
            self._sender_thread.start()

        if self._bidirectional or not self._sender:
            self._receiver_thread = Thread(target=self._core_receiver, daemon=True)
            self._receiver_thread.start()

        self.logger.log(f"StreamHandler for {self.stream_type} started.", Logger.DEBUG)

    def stop(self):
        """
        Stops the stream handler.
        """
        self._active = False
        if self._sender_thread and self._sender:
            try:
                self._sender_thread.join(timeout=2)
            except Exception as e:
                self.logger.log(f"Error stopping StreamHandler for {self.stream_type}: {e}", Logger.ERROR)

        if (self._bidirectional or not self._sender) and self._receiver_thread:
            try:
                self._receiver_thread.join(timeout=2)
            except Exception as e:
                self.logger.log(f"Error stopping StreamHandler for {self.stream_type}: {e}", Logger.ERROR)
        self.logger.log(f"StreamHandler for {self.stream_type} stopped.", Logger.DEBUG)

    def send(self, data: Any):
        """
        Queues data to be sent over the stream.
        """
        self._send_queue.put(data)

    def receive(self) -> Any:
        """
        Retrieves data received from the stream.
        """
        if not self._recv_queue.empty():
            return self._recv_queue.get()
        return None

    def _core_sender(self):
        """
        Core loop for handling sending data.
        """
        raise NotImplementedError

    def _core_receiver(self):
        """
        Core loop for handling receiving data.
        """
        raise NotImplementedError
