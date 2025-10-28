from queue import Queue
from threading import Thread
from typing import Any

from model.ClientObj import ClientsManager
from event.EventBus import EventBus
from utils.logging.logger import Logger

class StreamHandler:
    """
    A generic stream handler class for managing network streams.
    """

    def __init__(self, stream_type: int, clients: ClientsManager, event_bus: EventBus):
        self.stream_type = stream_type
        self.clients = clients
        self.event_bus = event_bus
        self._send_queue = Queue()
        self._active = False
        self._thread = None

        self.logger = Logger.get_instance()

    def start(self):
        """
        Starts the stream handler.
        """
        self._active = True
        self._thread = Thread(target=self._core, daemon=True)
        self._thread.start()
        self.logger.log(f"StreamHandler for {self.stream_type} started.", Logger.DEBUG)

    def stop(self):
        """
        Stops the stream handler.
        """
        self._active = False
        if self._thread:
            try:
                self._thread.join(timeout=2)
            except Exception as e:
                self.logger.log(f"Error stopping StreamHandler for {self.stream_type}: {e}", Logger.ERROR)
        self.logger.log(f"StreamHandler for {self.stream_type} stopped.", Logger.DEBUG)

    def send(self, data: Any):
        """
        Queues data to be sent over the stream.
        """
        self._send_queue.put(data)

    def _core(self):
        """
        Core loop for handling sending and receiving data.
        """
        raise NotImplementedError
