import asyncio
from typing import Optional

from utils.logging import Logger
from network.stream.GenericStream import StreamHandler
from network.data.MessageExchange import MessageExchange, MessageExchangeConfig
from network.connection.AsyncClientConnection import AsyncClientConnection
from model.ClientObj import ClientsManager, ClientObj

from event.EventBus import EventBus
from event import EventType


class UnidirectionalStreamHandler(StreamHandler):
    """
    A custom async stream handler for managing connection streams. (Unidirectional: Server -> Client)
    Fully async with optimized performance.
    """

    def __init__(self, stream_type: int, clients: ClientsManager, event_bus: EventBus, handler_id: Optional[str] = None,
                 source: str = "server", sender: bool = True):
        """
        Args:
            sender (bool): If True, the stream sends data.
        """
        super().__init__(stream_type=stream_type, clients=clients, event_bus=event_bus, sender=sender)

        self._active_client = None
        self.handler_id = handler_id if handler_id else f"UnidirectionalStreamHandler_{stream_type}"
        self.source = source

        # Create a MessageExchange object
        self.msg_exchange = MessageExchange(
            conf=MessageExchangeConfig(auto_dispatch=True),
            id=self.handler_id
        )

        self.logger = Logger.get_instance()

        # Subscribe with async callbacks
        self.event_bus.subscribe(event_type=EventType.ACTIVE_SCREEN_CHANGED, callback=self._on_active_screen_changed)
        self.event_bus.subscribe(event_type=EventType.CLIENT_DISCONNECTED, callback=self._on_client_disconnected)

    async def stop(self):
        await super().stop()
        await self.msg_exchange.stop()

    def register_receive_callback(self, receive_callback, message_type: str):
        """
        Register a callback function for receiving messages of a specific type.
        This is delegated to MessageExchange which handles async dispatch automatically.
        """
        self.msg_exchange.register_handler(message_type, receive_callback)

    async def _on_client_disconnected(self, data: dict):
        """
        Async event handler for when a client becomes inactive.
        """
        client_screen = data.get("client_screen")
        if self._active_client is not None and self._active_client.screen_position == client_screen:
            self._active_client = None
            await self.msg_exchange.set_transport(send_callback=None, receive_callback=None)

    async def _on_active_screen_changed(self, data: dict):
        """
        Async event handler for when the active screen changes.
        """
        # Get current active screen from event data
        active_screen = data.get("active_screen")

        # Find corresponding client
        self._active_client: Optional[ClientObj] = self.clients.get_client(screen_position=active_screen)

        # Set message exchange active client
        if self._active_client is not None:
            # Try to get corresponding stream socket
            cl_stram_socket = self._active_client.conn_socket
            if isinstance(cl_stram_socket, AsyncClientConnection):
                reader, writer = cl_stram_socket.get_stream(self.stream_type)

                # Setup transport callbacks asyncio
                async def async_send(data: bytes):
                    writer.write(data)
                    await writer.drain()

                async def async_recv(size: int) -> bytes:
                    return await reader.read(size)

                await self.msg_exchange.set_transport(
                    send_callback=async_send,
                    receive_callback=async_recv,
                )
                # Start msg exchange listener (always runs for async dispatch)
                await self.msg_exchange.start()
            else:
                self.logger.log(
                    f"{self.handler_id}: No valid stream for active client {self._active_client.screen_position}",
                    Logger.WARNING)
                await self.msg_exchange.set_transport(send_callback=None, receive_callback=None)
                await self.msg_exchange.stop()

            # Empty the send queue efficiently
            self._clear_buffer()
        else:
            await self.msg_exchange.set_transport(send_callback=None, receive_callback=None)
            await self.msg_exchange.stop()

    async def _core_sender(self):
        """
        Core async loop for handling stream sending with optimized batching.
        """
        while self._active:
            if self._active_client is not None and self._active_client.is_connected:
                try:
                    screen = self._active_client.screen_position # Before the first await to avoid missing active client
                    # Process sending queued data
                    data = await self._send_queue.get()
                    # If data is not dict call .to_dict()
                    if not isinstance(data, dict) and hasattr(data, "to_dict"):
                        data = data.to_dict()
                    await self.msg_exchange.send_stream_type_message(
                        stream_type=self.stream_type,
                        source=self.source,
                        target=screen,
                        **data
                    )

                except asyncio.TimeoutError:
                    await asyncio.sleep(self._waiting_time)
                    continue
                except Exception as e:
                    # Catch BrokenPipeError and ConnectionResetError separately if needed
                    if isinstance(e, (BrokenPipeError, ConnectionResetError)):
                        self.logger.log(f"Connection error in {self.handler_id}: {e}", Logger.WARNING)
                        # Set active client to None on connection errors
                        self._active_client = None
                    else:
                        self.logger.log(f"Error in {self.handler_id} core loop: {e}", Logger.ERROR)
                        await asyncio.sleep(self._waiting_time)
            else:
                await asyncio.sleep(self._waiting_time)


class BidirectionalStreamHandler(StreamHandler):
    """
    A custom async stream handler for managing bidirectional streams. Server <-> Client
    Fully async with optimized performance.
    """

    def __init__(self, stream_type: int, clients: ClientsManager, event_bus: EventBus, handler_id: Optional[str] = None,
                 source: str = "server"):
        """
        Args:
            source (str): The source identifier for messages.
        """
        super().__init__(stream_type=stream_type, clients=clients, event_bus=event_bus, sender=True)

        self._active_client = None
        self.handler_id = handler_id if handler_id else f"BidirectionalStreamHandler_{stream_type}"
        self.source = source

        # Create a MessageExchange object
        self.msg_exchange = MessageExchange(
            conf=MessageExchangeConfig(auto_dispatch=True),
            id=self.handler_id
        )

        self.logger = Logger.get_instance()

        # Subscribe with async callbacks
        self.event_bus.subscribe(event_type=EventType.ACTIVE_SCREEN_CHANGED, callback=self._on_active_screen_changed)
        self.event_bus.subscribe(event_type=EventType.CLIENT_DISCONNECTED, callback=self._on_client_disconnected)

    async def stop(self):
        await super().stop()
        await self.msg_exchange.stop()

    def register_receive_callback(self, receive_callback, message_type: str):
        """
        Register a callback function for receiving messages of a specific type.
        This is delegated to MessageExchange which handles async dispatch automatically.
        """
        self.msg_exchange.register_handler(message_type, receive_callback)

    async def _on_client_disconnected(self, data: dict):
        """
        Async event handler for when a client becomes inactive.
        """
        client_screen = data.get("client_screen")
        if self._active_client is not None and self._active_client.screen_position == client_screen:
            self._active_client = None
            self.logger.log(f"{self.handler_id}: Active client disconnected {client_screen}", Logger.INFO)
            await self.msg_exchange.set_transport(send_callback=None, receive_callback=None)

    async def _on_active_screen_changed(self, data: dict):
        """
        Async event handler for when the active screen changes.
        """
        # Get current active screen from event data
        active_screen = data.get("active_screen")

        # Find corresponding client
        self._active_client: Optional[ClientObj] = self.clients.get_client(screen_position=active_screen)

        # Set message exchange active client
        if self._active_client is not None:
            # Try to get corresponding stream socket
            cl_stram_socket = self._active_client.conn_socket
            if isinstance(cl_stram_socket, AsyncClientConnection):
                reader, writer = cl_stram_socket.get_stream(self.stream_type)

                # Setup transport callbacks asyncio
                async def async_send(data: bytes):
                    writer.write(data)
                    await writer.drain()

                async def async_recv(size: int) -> bytes:
                    return await reader.read(size)

                await self.msg_exchange.set_transport(
                    send_callback=async_send,
                    receive_callback=async_recv,
                )
                # Start msg exchange listener (always runs for async dispatch)
                await self.msg_exchange.start()
            else:
                self.logger.log(
                    f"{self.handler_id}: No valid stream for active client {self._active_client.screen_position}",
                    Logger.WARNING)
                await self.msg_exchange.set_transport(send_callback=None, receive_callback=None)
                await self.msg_exchange.stop()

            # Empty the send queue efficiently
            self._clear_buffer()
        else:
            await self.msg_exchange.set_transport(send_callback=None, receive_callback=None)
            await self.msg_exchange.stop()

    async def _core_sender(self):
        """
        Core async loop for handling stream sending with optimized batching.
        """
        while self._active:
            if self._active_client is not None and self._active_client.is_connected:
                try:
                    # Process sending queued data
                    data = await self._send_queue.get()
                    if not isinstance(data, dict) and hasattr(data, "to_dict"):
                        data = data.to_dict()
                    await self.msg_exchange.send_stream_type_message(
                        stream_type=self.stream_type,
                        source=self.source,
                        target=self._active_client.screen_position,
                        **data
                    )
                except asyncio.TimeoutError:
                    await asyncio.sleep(self._waiting_time)
                    continue
                except Exception as e:
                    self.logger.log(f"Error in {self.handler_id} core loop: {e}", Logger.ERROR)
                    await asyncio.sleep(self._waiting_time)
            else:
                await asyncio.sleep(self._waiting_time)

#TODO: Rename to MulticastStreamHandler?
#TODO: Similar to BidirectionalStreamHandler, maybe refactor common code into a base class or BidirectionalStreamHandler too
class MulticastStreamHandler(StreamHandler):
    """
    A custom async bidirectional stream handler for broadcasting messages to all connected clients,
    and receiving messages from any client.

    It will target all connected (not only active) clients.
    """

    def __init__(self, stream_type: int, clients: ClientsManager, event_bus: EventBus, handler_id: Optional[str] = None,
                 source: str = "server"):
        """
        Args:
            source (str): The source identifier for messages.
        """
        super().__init__(stream_type=stream_type, clients=clients, event_bus=event_bus, sender=True)

        self._active_client = None
        self.handler_id = handler_id if handler_id else f"UnidirectionalStreamHandler_{stream_type}"
        self.source = source

        # Create a MessageExchange object (sending only, no receiving needed)
        self.msg_exchange = MessageExchange(
            conf=MessageExchangeConfig(auto_dispatch=True),
            id=self.handler_id
        )

        self._clients_connected = 0

        self.logger = Logger.get_instance()

        # Subscribe with async callbacks
        self.event_bus.subscribe(event_type=EventType.CLIENT_CONNECTED, callback=self._on_client_connected)
        self.event_bus.subscribe(event_type=EventType.CLIENT_DISCONNECTED, callback=self._on_client_disconnected)
        self.event_bus.subscribe(event_type=EventType.ACTIVE_SCREEN_CHANGED, callback=self._on_active_screen_changed)

    def register_receive_callback(self, receive_callback, message_type: str):
        """
        Register a callback function for receiving messages of a specific type.
        This is delegated to MessageExchange which handles async dispatch automatically.
        """
        self.msg_exchange.register_handler(message_type, receive_callback)

    async def _on_client_connected(self, data: dict):
        self._clients_connected += 1

    async def _on_client_disconnected(self, data: dict):
        self._clients_connected -= 1

        client_screen = data.get("client_screen")
        if self._active_client is not None and self._active_client.screen_position == client_screen:
            self._active_client = None
            self.logger.log(f"{self.handler_id}: Active client disconnected {client_screen}", Logger.INFO)
            await self.msg_exchange.set_transport(send_callback=None, receive_callback=None)

    async def _on_active_screen_changed(self, data: dict):
        """
        Async event handler for when the active screen changes.
        """
        # Get current active screen from event data
        active_screen = data.get("active_screen")

        # Find corresponding client
        self._active_client: Optional[ClientObj] = self.clients.get_client(screen_position=active_screen)

        # Set message exchange active client
        if self._active_client is not None:
            # Try to get corresponding stream socket
            cl_stram_socket = self._active_client.conn_socket
            if isinstance(cl_stram_socket, AsyncClientConnection):
                reader, writer = cl_stram_socket.get_stream(self.stream_type)

                # Setup transport callbacks asyncio
                async def async_send(data: bytes):
                    writer.write(data)
                    await writer.drain()

                async def async_recv(size: int) -> bytes:
                    return await reader.read(size)

                await self.msg_exchange.set_transport(
                    send_callback=async_send,
                    receive_callback=async_recv,
                )
                # Start msg exchange listener (always runs for async dispatch)
                await self.msg_exchange.start()
            else:
                self.logger.log(
                    f"{self.handler_id}: No valid stream for active client {self._active_client.screen_position}",
                    Logger.WARNING)
                await self.msg_exchange.set_transport(send_callback=None, receive_callback=None)
                await self.msg_exchange.stop()

            # Empty the send queue efficiently
            self._clear_buffer()
        else:
            await self.msg_exchange.set_transport(send_callback=None, receive_callback=None)
            await self.msg_exchange.stop()

    async def _core_sender(self):
        """
        Core async loop for handling stream sending with optimized batching.
        """
        while self._active:
            if self._clients_connected > 0:
                try:
                    # Process sending queued data
                    data = await self._send_queue.get()
                    # If data is not dict call .to_dict()
                    if not isinstance(data, dict) and hasattr(data, "to_dict"):
                        data = data.to_dict()

                    # Broadcast to all connected clients
                    for client in self.clients.get_clients():
                        if client.is_connected:
                            cl_stram_socket = client.conn_socket
                            if isinstance(cl_stram_socket, AsyncClientConnection):
                                _, writer = cl_stram_socket.get_stream(self.stream_type)

                                # Setup transport callbacks asyncio
                                async def async_send(d: bytes):
                                    writer.write(d)
                                    await writer.drain()

                                await self.msg_exchange.set_transport(
                                    send_callback=async_send,
                                    receive_callback=None,
                                )

                                await self.msg_exchange.send_stream_type_message(
                                    stream_type=self.stream_type,
                                    source=self.source,
                                    target=client.screen_position,
                                    **data
                                )
                    # Clear transport after broadcasting
                    await self.msg_exchange.set_transport(send_callback=None, receive_callback=None)

                except asyncio.TimeoutError:
                    await asyncio.sleep(self._waiting_time)
                    continue
                except Exception as e:
                    self.logger.log(f"Error in {self.handler_id} core loop: {e}", Logger.ERROR)
                    await asyncio.sleep(self._waiting_time)
            else:
                await asyncio.sleep(self._waiting_time)

    async def stop(self):
        await super().stop()