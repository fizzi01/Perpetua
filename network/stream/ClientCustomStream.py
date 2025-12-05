import asyncio
from typing import Optional

from utils.logging import Logger
from network.connection.AsyncClientConnection import AsyncClientConnection
from network.stream.GenericStream import StreamHandler
from network.data.MessageExchange import MessageExchange, MessageExchangeConfig
from model.ClientObj import ClientsManager, ClientObj

from event.EventBus import EventBus
from event import EventType


class UnidirectionalStreamHandler(StreamHandler):
    """
    A custom async stream handler for managing connection streams. (Unidirectional: Client -> Server)
    Fully async with optimized performance.
    """

    def __init__(self, stream_type: int, clients: ClientsManager, event_bus: EventBus, handler_id: Optional[str] = None,
                 sender: bool = True, active_only: bool = False):
        super().__init__(stream_type=stream_type, clients=clients, event_bus=event_bus, sender=sender)

        self._is_active = False # Track if current client is active

        self.handler_id = handler_id if handler_id else f"UnidirectionalStreamHandler_{stream_type}"
        self._active_only = active_only

        # Create a MessageExchange object
        self.msg_exchange = MessageExchange(
            conf=MessageExchangeConfig(auto_dispatch=True),
            id=self.handler_id
        )

        # Get main client
        # If client manager is correctly initialized, it should have only one main client
        self._main_client: Optional[ClientObj] = self.clients.get_client()

        if self._main_client is None:
            raise ValueError(f"No main client found in ClientsManager for {self.handler_id}")

        self.logger = Logger.get_instance()

        # Subscribe with async callbacks
        event_bus.subscribe(event_type=EventType.CLIENT_ACTIVE, callback=self._on_client_active)
        event_bus.subscribe(event_type=EventType.CLIENT_INACTIVE, callback=self._on_client_inactive)

    async def stop(self):
        await super().stop()
        await self.msg_exchange.stop()

    async def _on_client_active(self, data: dict):
        """
        Async event handler for when a client becomes active.
        """
        if self._is_active:
            return
        self._is_active = True

        # Set message exchange transport source
        cl_stram_socket = self._main_client.conn_socket
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
            self.logger.log(f"{self.handler_id} - Client is active", Logger.DEBUG)
        else:
            self.logger.log(f"Invalid connection socket for main client in {self.handler_id}", Logger.ERROR)
            raise ValueError(f"Invalid connection socket for main client in {self.handler_id}")

    async def _on_client_inactive(self, data: dict):
        """
        Async event handler for when a client becomes inactive.
        """
        self._is_active = False
        # Stop msg exchange listener
        await self.msg_exchange.stop()
        self.logger.log(f"{self.handler_id} - Client is inactive", Logger.DEBUG)

    def register_receive_callback(self, receive_callback, message_type: str):
        """
        Register a callback function for receiving messages of a specific type.
        This is delegated to MessageExchange which handles async dispatch automatically.
        """
        self.msg_exchange.register_handler(message_type, receive_callback)

    async def _core_sender(self):
        """
        Core async sender loop for sending messages to the server with optimizations.
        """
        while self._active:
            if self._active_only and not self._is_active:
                await asyncio.sleep(self._waiting_time)
                continue

            try:
                # Get data from queue
                data = await self._send_queue.get()
                if not isinstance(data, dict) and hasattr(data, "to_dict"):
                    data = data.to_dict()
                await self.msg_exchange.send_stream_type_message(
                    stream_type=self.stream_type,
                    source=self._main_client.screen_position,
                    target="server",
                    **data
                )
            except asyncio.TimeoutError:
                await asyncio.sleep(self._waiting_time)
                continue
            except Exception as e:
                self.logger.log(f"Error in {self.handler_id} core loop: {e}", Logger.ERROR)
                await asyncio.sleep(self._waiting_time)


class BidirectionalStreamHandler(StreamHandler):
    """
    A custom async stream handler for managing bidirectional streams. Client <-> Server
    Fully async with optimized performance.
    """

    def __init__(self, stream_type: int, clients: ClientsManager, event_bus: EventBus, handler_id: Optional[str] = None,
                 active_only: bool = False, avoid_receive_stop: bool = True):
        super().__init__(stream_type=stream_type, clients=clients, event_bus=event_bus, sender=True)

        self._is_active = False # Track if current client is active

        self.handler_id = handler_id if handler_id else f"BidirectionalStreamHandler_{stream_type}"
        self._active_only = active_only
        self._avoid_receive_stop = avoid_receive_stop

        # Create a MessageExchange object
        self.msg_exchange = MessageExchange(
            conf=MessageExchangeConfig(auto_dispatch=True),
            id=self.handler_id
        )

        # Get main client
        # If client manager is correctly initialized, it should have only one main client
        self._main_client: Optional[ClientObj] = None

        self.logger = Logger()

        # Subscribe with async callbacks
        event_bus.subscribe(event_type=EventType.CLIENT_ACTIVE, callback=self._on_client_active)
        event_bus.subscribe(event_type=EventType.CLIENT_INACTIVE, callback=self._on_client_inactive)

    async def stop(self):
        await super().stop()
        await self.msg_exchange.stop()

    async def _on_client_active(self, data: dict):
        """
        Async event handler for when a client becomes active.
        """
        if self._active:
            return

        self.logger.log(f"{self.handler_id} - Client is active", Logger.DEBUG)
        self._is_active = True

        self._main_client = self.clients.get_client()

        if self._main_client is None:
            raise ValueError(f"No main client found in ClientsManager for {self.handler_id}")
        # Set message exchange transport source
        cl_stram_socket = self._main_client.conn_socket
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
            raise ValueError(f"Invalid connection socket for main client in {self.handler_id}")

    async def _on_client_inactive(self, data: dict):
        """
        Async event handler for when a client becomes inactive.
        """
        if not self._avoid_receive_stop:
            self._is_active = False
            # Stop msg exchange listener
            await self.msg_exchange.stop()

    def register_receive_callback(self, receive_callback, message_type: str):
        """
        Register a callback function for receiving messages of a specific type.
        This is delegated to MessageExchange which handles async dispatch automatically.
        """
        self.msg_exchange.register_handler(message_type, receive_callback)


    async def _core_sender(self):
        """
        Core async sender loop for sending messages to the server with optimizations.
        """
        while self._active:
            if self._active_only and not self._is_active:
                await asyncio.sleep(self._waiting_time)
                continue

            try:
                # Get data from queue
                data = await self._send_queue.get()
                if not isinstance(data, dict) and hasattr(data, "to_dict"):
                    data = data.to_dict()
                await self.msg_exchange.send_stream_type_message(
                    stream_type=self.stream_type,
                    source=self._main_client.screen_position,
                    target="server",
                    **data
                )
            except asyncio.TimeoutError:
                await asyncio.sleep(self._waiting_time)
                continue
            except Exception as e:
                self.logger.log(f"Error in {self.handler_id} core loop: {e}", Logger.ERROR)
                await asyncio.sleep(self._waiting_time)
