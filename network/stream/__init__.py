import asyncio
from enum import IntEnum
from typing import Any

from event.bus import EventBus
from model.client import ClientsManager
from utils.logging import Logger, get_logger


class StreamType(IntEnum):
    """
    Enumeration of different stream types with priority levels.
    """

    COMMAND = 0  # High priority - bidirectional commands
    KEYBOARD = 4  # High priority - keyboard events
    MOUSE = 1  # High priority - mouse movements (high frequency)
    CLIPBOARD = 12  # Low priority - clipboard
    FILE = 16  # Low priority - file transfers

    @classmethod
    def is_valid(cls, stream_type: int) -> bool:
        """
        Verify if the given stream type is valid.
        """
        try:
            cls(stream_type)
            return True
        except ValueError:
            return False


class StreamHandler:
    """
    A generic async stream handler class for managing network streams.
    """

    def __init__(
        self,
        stream_type: int,
        clients: ClientsManager,
        event_bus: EventBus,
        sender: bool = True,
        buffer_size: int = 1000,
    ):
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
        self._send_queue: asyncio.Queue = asyncio.Queue(maxsize=buffer_size)
        self._active = False
        self._sender_task = None

        self._sender = sender

        self._waiting_time = 0  # Time to wait in loops to prevent busy waiting

        self._logger = get_logger(self.__class__.__name__)

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
        if self._active:
            return True

        self._active = True
        if self._sender:
            self._sender_task = asyncio.create_task(self._core_sender())

        self._logger.log(f"StreamHandler for {self.stream_type} started.", Logger.DEBUG)
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
                    self._logger.log(
                        f"StreamHandler for {self.stream_type} sender task did not stop in time",
                        Logger.WARNING,
                    )
            except Exception as e:
                self._logger.log(
                    f"Error stopping StreamHandler for {self.stream_type}: {e}",
                    Logger.ERROR,
                )
                return False

        self._logger.log(f"StreamHandler for {self.stream_type} stopped.", Logger.DEBUG)
        return True

    def is_active(self) -> bool:
        """
        Returns whether the stream handler is active.
        """
        return self._active

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
