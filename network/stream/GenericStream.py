import asyncio
from typing import Any

from model.ClientObj import ClientsManager
from event.EventBus import EventBus
from utils.logging import Logger


class StreamHandler:
    """
    A generic async stream handler class for managing network streams.
    """

    def __init__(self, stream_type: int, clients: ClientsManager, event_bus: EventBus,
                 sender: bool = True):
        """
        Attributes:
            stream_type (int): The type of stream (e.g., mouse, keyboard, command).
            clients (ClientsManager): Manager for connected clients.
            event_bus (EventBus): Event bus for handling events.
            sender (bool): If True, the stream sends data.
        """
        self.stream_type = stream_type
        self.clients = clients
        self.event_bus = event_bus
        self._send_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._active = False
        self._sender_task = None

        self._sender = sender

        self._waiting_time = 0  # Time to wait in loops to prevent busy waiting

        self.logger = Logger.get_instance()

    def register_receive_callback(self, receive_callback, message_type: str):
        """
        Register a callback function for receiving messages of a specific type.
        This is now handled by MessageExchange directly.
        """
        raise NotImplementedError

    async def start(self) -> bool:
        """
        Starts the stream handler.
        """
        self._active = True
        if self._sender:
            self._sender_task = asyncio.create_task(self._core_sender())

        self.logger.log(f"StreamHandler for {self.stream_type} started.", Logger.DEBUG)
        return True

    async def stop(self) -> bool:
        """
        Stops the stream handler.
        """
        self._active = False
        if self._sender_task and self._sender:
            try:
                self._sender_task.cancel()
                try:
                    await asyncio.wait_for(self._sender_task, timeout=2.0)
                except asyncio.CancelledError:
                    pass
                except asyncio.TimeoutError:
                    self.logger.log(f"StreamHandler for {self.stream_type} sender task did not stop in time", Logger.WARNING)
            except Exception as e:
                self.logger.log(f"Error stopping StreamHandler for {self.stream_type}: {e}", Logger.ERROR)
                return False

        self.logger.log(f"StreamHandler for {self.stream_type} stopped.", Logger.DEBUG)
        return True

    async def send(self, data: Any):
        """
        Queues data to be sent over the stream.
        """
        await self._send_queue.put(data)

    def _clear_buffer(self):
        """
        Clears the send queue.
        """
        while not self._send_queue.empty():
            try:
                self._send_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    async def _core_sender(self):
        """
        Core loop for handling sending data.
        """
        raise NotImplementedError

