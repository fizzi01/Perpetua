import asyncio
from typing import Optional

from network.data.exchange import MessageExchange, MessageExchangeConfig
from model.client import ClientsManager

from event.bus import EventBus
from event import (
    EventType,
    ActiveScreenChangedEvent,
    ClientDisconnectedEvent,
    ClientConnectedEvent,
    ClientStreamReconnectedEvent,
)

from utils.metrics import MetricsCollector

from . import _ServerStreamHandler


class UnidirectionalStreamHandler(_ServerStreamHandler):
    """
    A custom async stream handler for managing connection streams. (Unidirectional: Server -> Client)
    Fully async with optimized performance.
    """

    async def _on_streams_reconnected(
        self, data: Optional[ClientStreamReconnectedEvent]
    ):
        """
        Async event handler for when a client stream reconnects.
        """
        if data is None:
            return

        # Check if the reconnected stream type matches this handler's stream type
        if self.stream_type not in data.streams:
            return

        self._logger.debug(f"Stream {self.stream_type} reconnected")

        client_screen = data.client_screen
        if (
            self._active_client is not None
            and self._active_client.get_screen_position() == client_screen
        ):
            try:
                if not await self._configure_stream_transport_for_client(
                    client=self._active_client,
                    stream_type=self.stream_type,
                    msg_exchange=self.msg_exchange,
                    transport_id=None,
                ):
                    # If send only, we disable the active client if no valid transport
                    if self._sender:
                        self._active_client = None
            finally:
                self._clear_buffer()

    async def _on_client_disconnected(self, data: Optional[ClientDisconnectedEvent]):
        """
        Async event handler for when a client becomes inactive.
        """
        if data is None:
            return

        client_screen = data.client_screen
        if (
            self._active_client is not None
            and self._active_client.get_screen_position() == client_screen
        ):
            try:
                self._active_client = None
                await self.msg_exchange.set_transport(
                    send_callback=None, receive_callback=None
                )
            finally:
                self._clear_buffer()

    async def _on_active_screen_changed(self, data: Optional[ActiveScreenChangedEvent]):
        """
        Async event handler for when the active screen changes.
        """
        if data is None:
            return

        try:
            # Get current active screen from event data
            active_screen = data.active_screen

            # Find corresponding client
            self._active_client = self.clients.get_client(screen_position=active_screen)

            # Set message exchange active client
            if self._active_client is not None:
                self._clear_buffer()
                if not await self._configure_stream_transport_for_client(
                    client=self._active_client,
                    stream_type=self.stream_type,
                    msg_exchange=self.msg_exchange,
                    transport_id=None,
                ):
                    # If send only, we disable the active client if no valid transport
                    if self._sender:
                        self._active_client = None
        finally:
            self._clear_buffer()

    def _send_clause(self) -> bool:
        return self._active_client is not None and self._active_client.is_connected


class BidirectionalStreamHandler(_ServerStreamHandler):
    """
    A custom async stream handler for managing bidirectional streams. Server <-> Client
    Fully async with optimized performance.
    """

    def __init__(
        self,
        stream_type: int,
        clients: ClientsManager,
        event_bus: EventBus,
        handler_id: Optional[str] = None,
        source: str = "server",
        metrics_collector: Optional[MetricsCollector] = None,
        buffer_size: int = 1000,
    ):
        """
        Initializes and configures an instance responsible for managing the interaction between
        a specified stream type, connected clients, and event-driven communication. This
        initialization involves creating necessary message exchange components, setting up logging,
        and subscribing to specific events.

        Args:
            stream_type: (int): Represents the type of the stream being managed.
            clients: (ClientsManager): Instance managing client connections and their state.
            event_bus: (EventBus): Centralized event bus for subscribing and broadcasting events.
            handler_id: (Optional[str]): Unique identifier for the handler; defaults to a derived
                value based on class name and stream type if not provided.
            source: (str): Identifier indicating the origin of the handler, defaulting to "server".
            metrics_collector: (Optional[MetricsCollector]): Optional metrics collector for
            buffer_size: (int): Size of the internal buffer for managing outgoing messages.

        Configurations:
            Automatically dispatches messages using the MessageExchange component.
            Subscribes to events via the event bus for handling client lifecycle and screen-related
            actions, such as:
                - ACTIVE_SCREEN_CHANGED
                - CLIENT_DISCONNECTED
                - CLIENT_STREAM_RECONNECTED
        """
        super().__init__(
            stream_type=stream_type,
            clients=clients,
            event_bus=event_bus,
            handler_id=handler_id,
            source=source,
            sender=True,
            metrics_collector=metrics_collector,
            buffer_size=buffer_size,
        )

    async def _on_client_disconnected(self, data: Optional[ClientDisconnectedEvent]):
        """
        Async event handler for when a client becomes inactive.
        """
        if data is None:
            return

        client_screen = data.client_screen
        if (
            self._active_client is not None
            and self._active_client.screen_position == client_screen
        ):
            self._active_client = None
            # self.logger.debug(f"Active client disconnected {client_screen}")
            await self.msg_exchange.set_transport(
                send_callback=None, receive_callback=None
            )

    async def _on_streams_reconnected(
        self, data: Optional[ClientStreamReconnectedEvent]
    ):
        """
        Async event handler for when a client stream reconnects.
        """
        if data is None:
            return

        # Check if the reconnected stream type matches this handler's stream type
        if self.stream_type not in data.streams:
            return

        self._logger.debug(f"Stream {self.stream_type} reconnected")

        client_screen = data.client_screen
        if (
            self._active_client is not None
            and self._active_client.get_screen_position() == client_screen
        ):
            try:
                if not await self._configure_stream_transport_for_client(
                    client=self._active_client,
                    stream_type=self.stream_type,
                    msg_exchange=self.msg_exchange,
                    transport_id=None,
                ):
                    self._active_client = None
            finally:
                self._clear_buffer()

    async def _on_active_screen_changed(self, data: Optional[ActiveScreenChangedEvent]):
        """
        Async event handler for when the active screen changes.
        """
        if data is None:
            return

        try:
            # Get current active screen from event data
            active_screen = data.active_screen

            # Find corresponding client
            self._active_client = self.clients.get_client(screen_position=active_screen)

            # Set message exchange active client
            if self._active_client is not None:
                self._clear_buffer()
                if not await self._configure_stream_transport_for_client(
                    client=self._active_client,
                    stream_type=self.stream_type,
                    msg_exchange=self.msg_exchange,
                    transport_id=None,
                ):
                    self._active_client = None
        finally:
            self._clear_buffer()

    def _send_clause(self) -> bool:
        return self._active_client is not None and self._active_client.is_connected


# TODO: Similar to BidirectionalStreamHandler, maybe refactor common code into a base class or BidirectionalStreamHandler too
class MulticastStreamHandler(_ServerStreamHandler):
    """
    A custom async bidirectional stream handler for broadcasting messages to all connected clients,
    and receiving messages from any client.

    It will target all connected (not only active) clients.
    """

    def __init__(
        self,
        stream_type: int,
        clients: ClientsManager,
        event_bus: EventBus,
        handler_id: Optional[str] = None,
        source: str = "server",
        metrics_collector: Optional[MetricsCollector] = None,
        buffer_size: int = 1000,
    ):
        """
        It initializes an instance responsible for managing bidirectional communication
        across multiple clients for a specified stream type. This involves setting up message
        exchange mechanisms, logging, and event subscriptions to handle client connections
        and active screen changes.

        Args:
            stream_type (int): The type of stream being used.
            clients (ClientsManager): Manager for handling clients in the
                system.
            event_bus (EventBus): EventBus instance for subscribing to and
                publishing events.
            handler_id (Optional[str]): Optional ID for the handler. If not
                provided, an ID is generated using the handler class name and
                stream type.
            source (str): Identifier specifying the source of the stream,
                defaults to "server".


        Configurations:
            Automatically dispatches messages using the MessageExchange component.
            Subscribes to events via the event bus for handling client lifecycle and screen-related
            actions, such as:
                - CLIENT_CONNECTED
                - CLIENT_DISCONNECTED
                - CLIENT_STREAM_RECONNECTED
        """
        super().__init__(
            stream_type=stream_type,
            clients=clients,
            event_bus=event_bus,
            handler_id=handler_id,
            source=source,
            sender=True,
            metrics_collector=metrics_collector,
            buffer_size=buffer_size,
        )

        self._clients_connected = 0

    def _build_exchange(
        self, metrics_collector: Optional[MetricsCollector]
    ) -> MessageExchange:
        return MessageExchange(
            conf=MessageExchangeConfig(auto_dispatch=True, multicast=True),
            id=self.handler_id,
            metrics_collector=metrics_collector,
        )

    def _bus_subscribe(self):
        # Subscribe with async callbacks
        # We don't need active screen changed for multicast
        # self.event_bus.subscribe(event_type=EventType.ACTIVE_SCREEN_CHANGED, callback=self._on_active_screen_changed)

        self.event_bus.subscribe(
            event_type=EventType.CLIENT_CONNECTED, callback=self._on_client_connected
        )
        self.event_bus.subscribe(
            event_type=EventType.CLIENT_DISCONNECTED,
            callback=self._on_client_disconnected,
        )
        self.event_bus.subscribe(
            event_type=EventType.CLIENT_STREAM_RECONNECTED,
            callback=self._on_streams_reconnected,
        )

    async def _on_streams_reconnected(
        self, data: Optional[ClientStreamReconnectedEvent]
    ):
        """
        Async event handler for when a client stream reconnects.
        """
        if data is None:
            return

        # Check if the reconnected stream type matches this handler's stream type
        if self.stream_type not in data.streams:
            return

        self._logger.debug(f"Stream {self.stream_type} reconnected")

        client_screen = data.client_screen
        try:
            client = self.clients.get_client(screen_position=client_screen)
            if client is not None:
                cl_conn = client.get_connection()
                if cl_conn is not None:
                    cl_stream = cl_conn.get_stream(self.stream_type)
                    if cl_stream is None:
                        await asyncio.sleep(0)
                        return

                    await self.msg_exchange.set_transport(
                        send_callback=cl_stream.get_writer_call(),
                        receive_callback=cl_stream.get_reader_call(),
                        tr_id=client_screen,
                    )
        except Exception as e:
            self._logger.error(
                f"Error configuring transport for reconnected stream -> {e}"
            )

    async def _on_client_connected(self, data: Optional[ClientConnectedEvent]):
        self._clients_connected += 1

        if data is None:
            return

        try:
            client_screen = data.client_screen
            # Because multicast, we set_transport for each connected client
            client = self.clients.get_client(screen_position=client_screen)
            if client is not None:
                cl_conn = client.get_connection()
                if cl_conn is not None:
                    cl_stream = cl_conn.get_stream(self.stream_type)
                    if cl_stream is None:
                        await asyncio.sleep(0)
                        return

                    await self.msg_exchange.set_transport(
                        send_callback=cl_stream.get_writer_call(),
                        receive_callback=cl_stream.get_reader_call(),
                        tr_id=client_screen,
                    )
        finally:
            if self._clients_connected == 1:
                await self.msg_exchange.start()

    async def _on_client_disconnected(self, data: Optional[ClientDisconnectedEvent]):
        self._clients_connected -= 1

        if data is None:
            return

        client_screen = data.client_screen
        try:
            # self.logger.log(f"Active client disconnected {client_screen}", Logger.INFO)
            await self.msg_exchange.set_transport(
                send_callback=None, receive_callback=None, tr_id=client_screen
            )
        finally:
            if self._clients_connected == 0:
                await self.msg_exchange.stop()
                self._clear_buffer()

    async def _on_active_screen_changed(self, data: Optional[ActiveScreenChangedEvent]):
        """
        Async event handler for when the active screen changes.
        """
        if data is None:
            return

        try:
            # Get current active screen from event data
            active_screen = data.active_screen

            # Find corresponding client
            self._active_client = self.clients.get_client(screen_position=active_screen)

            # Set message exchange active client
            if self._active_client is not None:
                if not await self._configure_stream_transport_for_client(
                    client=self._active_client,
                    stream_type=self.stream_type,
                    msg_exchange=self.msg_exchange,
                    transport_id=active_screen,
                ):
                    # If send enabled, we disable the active client if no valid transport
                    self._active_client = None
        finally:
            self._clear_buffer()

    def _send_clause(self) -> bool:
        return self._clients_connected > 0

    async def _send_logic(self):
        # Process sending queued data
        data = await self._send_queue.get()
        # If data is not dict call .to_dict()
        if not isinstance(data, dict) and hasattr(data, "to_dict"):
            data = data.to_dict()

        await self.msg_exchange.send_stream_type_message(
            stream_type=self.stream_type,
            source=self.source,
            # target=client.screen_position, # Let the multicast message exchange handle all targets
            **data,
        )

    async def start(self) -> bool:
        st = await super().start()
        if self._clients_connected > 0:
            await self.msg_exchange.start()
        return st
