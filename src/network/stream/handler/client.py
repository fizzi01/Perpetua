from typing import Optional

from event.bus import EventBus
from event import ClientActiveEvent, ClientStreamReconnectedEvent
from model.client import ClientsManager

from utils.metrics import MetricsCollector

from . import _ClientStreamHandler


class UnidirectionalStreamHandler(_ClientStreamHandler):
    """
    A custom stream handler for managing connection streams. (Unidirectional: Client -> Server)
    """

    async def _on_client_active(self, data: Optional[ClientActiveEvent]):
        """
        Async event handler for when a client becomes active.
        """
        if self._is_active:
            return

        self._is_active = True
        self._clear_buffer()  # Clear buffer before configuring transport

        try:
            # Retrieve main client
            self._main_client = self.clients.get_client()
            if self._main_client is None:
                self._logger.error("No main client found in ClientsManager")
                self._is_active = False
                await self.msg_exchange.stop()  # Just in case
                return

            if not await self._configure_stream_transport_for_client(
                client=self._main_client,
                stream_type=self.stream_type,
                msg_exchange=self.msg_exchange,
                transport_id=None,
            ):
                self._is_active = False
                return
        finally:
            self._clear_buffer()

    async def _on_client_inactive(self, data: Optional[ClientActiveEvent]):
        """
        Async event handler for when a client becomes inactive.
        """
        try:
            self._is_active = False
            # Stop msg exchange listener
            await self.msg_exchange.stop()
            # self.logger.debug(f"[{self.handler_id}] Client is inactive")
        finally:
            self._clear_buffer()

    async def _on_streams_reconnected(
        self, data: Optional[ClientStreamReconnectedEvent]
    ):
        if data is None:
            return

        # Check if the reconnected stream type matches this handler's stream type
        if self.stream_type not in data.streams:
            return

        self._logger.debug(f"Stream {self.stream_type} reconnected")

        # Reconfigure the transport for the main client
        if self._main_client is None:
            self._main_client = self.clients.get_client()
            if self._main_client is None:
                self._logger.error("No main client found in ClientsManager")
                return

        if not await self._configure_stream_transport_for_client(
            client=self._main_client,
            stream_type=self.stream_type,
            msg_exchange=self.msg_exchange,
            transport_id=None,
        ):
            self._is_active = False
            return


class BidirectionalStreamHandler(_ClientStreamHandler):
    """
    A custom stream handler for managing bidirectional streams. Client <-> Server
    """

    def __init__(
        self,
        stream_type: int,
        clients: ClientsManager,
        event_bus: EventBus,
        handler_id: Optional[str] = None,
        active_only: bool = False,
        metrics_collector: Optional[MetricsCollector] = None,
        buffer_size: int = 1000,
    ):
        """
        It handles bidirectional stream management and event subscription for the client.

        Args:
            stream_type (int): Indicates the type of the stream.
            clients (ClientsManager): Manages client connections and interactions.
            event_bus (EventBus): The event handling system to subscribe and listen for events.
            handler_id (Optional[str]): Unique identifier for the handler. If not provided, it
                generates one based on the stream type and class name.
            active_only (bool): If True, only handles events when the client is active. Defaults to False.
            metrics_collector (Optional[MetricsCollector]): Collector for gathering metrics data. Defaults to None.
            buffer_size (int): Size of the internal buffer for sending messages. Defaults to 1000.
        """
        super().__init__(
            stream_type=stream_type,
            clients=clients,
            event_bus=event_bus,
            handler_id=handler_id,
            active_only=active_only,
            sender=True,  # Bidirectional always sends
            metrics_collector=metrics_collector,
            buffer_size=buffer_size,
        )

    async def _on_client_active(self, data: Optional[ClientActiveEvent]):
        """
        Async event handler for when a client becomes active.
        """
        if self._is_active:
            return

        self._is_active = True

        try:
            # Retrieve main client
            self._main_client = self.clients.get_client()
            if self._main_client is None:
                self._logger.error("No main client found in ClientsManager")
                self._is_active = False
                await self.msg_exchange.stop()  # Just in case
                return

            if not await self._configure_stream_transport_for_client(
                client=self._main_client,
                stream_type=self.stream_type,
                msg_exchange=self.msg_exchange,
                transport_id=None,
            ):
                self._is_active = False
                return
        finally:
            self._clear_buffer()

    async def _on_client_inactive(self, data: Optional[ClientActiveEvent]):
        """
        Async event handler for when a client becomes inactive.
        """
        # We stop sending, if active_only is set
        if self._active_only:
            self._is_active = False
            self._clear_buffer()
        # await self.msg_exchange.set_transport(send_callback=None, receive_callback=None)
        # await self.msg_exchange.stop()

    async def _on_streams_reconnected(
        self, data: Optional[ClientStreamReconnectedEvent]
    ):
        if data is None:
            return

        # Check if the reconnected stream type matches this handler's stream type
        if self.stream_type not in data.streams:
            return

        self._logger.debug(f"Stream {self.stream_type} reconnected")

        # Reconfigure the transport for the main client
        if self._main_client is None:
            self._main_client = self.clients.get_client()
            if self._main_client is None:
                self._logger.error("No main client found in ClientsManager")
                return

        if not await self._configure_stream_transport_for_client(
            client=self._main_client,
            stream_type=self.stream_type,
            msg_exchange=self.msg_exchange,
            transport_id=None,
        ):
            self._is_active = False
            return
