import asyncio
from typing import Optional

from utils.logging import Logger, get_logger
from network.stream import StreamHandler
from network.data.exchange import MessageExchange, MessageExchangeConfig
from model.client import ClientsManager, ClientObj

from event.bus import EventBus
from event import EventType, ActiveScreenChangedEvent, ClientDisconnectedEvent, ClientConnectedEvent


class UnidirectionalStreamHandler(StreamHandler):
    """
    A custom async stream handler for managing connection streams. (Unidirectional: Server -> Client)
    Fully async with optimized performance.
    """

    def __init__(self, stream_type: int, clients: ClientsManager, event_bus: EventBus, handler_id: Optional[str] = None,
                 source: str = "server", sender: bool = True):
        """
        Initializes and configures an instance responsible for managing the interaction between
        a specified stream type, connected clients, and event-driven communication. This
        initialization involves creating necessary message exchange components, setting up logging,
        and subscribing to specific events.

        Parameters:
            stream_type (int): Represents the type of the stream being managed.
            clients (ClientsManager): Instance managing client connections and their state.
            event_bus (EventBus): Centralized event bus for subscribing and broadcasting events.
            handler_id (Optional[str]): Unique identifier for the handler; defaults to a derived
                value based on class name and stream type if not provided.
            source (str): Identifier indicating the origin of the handler, defaulting to "server".
            sender (bool): Determines whether the handler acts as a sender, default is True.

        Attributes:
            _active_client (Optional[ClientObj]): The currently active client being handled or
                None if no client is active.
            handler_id (str): Unique identifier derived or assigned to the handler for its operation.
            source (str): Identifier for the handler's origin, allowing differentiation based on
                source type.
            msg_exchange (MessageExchange): Facilitates the exchange of messages with auto-dispatch
                configuration enabled.
            _logger (Logger): Logger instance for logging events and data specific to this handler.

        Configurations:
            Automatically dispatches messages using the MessageExchange component.
            Subscribes to events via the event bus for handling client lifecycle and screen-related
            actions, such as:
                - ACTIVE_SCREEN_CHANGED
                - CLIENT_DISCONNECTED
        """
        super().__init__(stream_type=stream_type, clients=clients, event_bus=event_bus, sender=sender)

        self._active_client: Optional[ClientObj] = None
        self.handler_id = handler_id if handler_id else f"{self.__class__.__name__}-{self.stream_type}"
        self.source = source

        # Create a MessageExchange object
        self.msg_exchange = MessageExchange(
            conf=MessageExchangeConfig(auto_dispatch=True),
            id=self.handler_id
        )

        self._logger = get_logger(self.handler_id)

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

    async def _on_client_disconnected(self, data: Optional[ClientDisconnectedEvent]):
        """
        Async event handler for when a client becomes inactive.
        """
        if data is None:
            return

        client_screen = data.client_screen
        if self._active_client is not None and self._active_client.get_screen_position() == client_screen:
            try:
                self._active_client = None
                await self.msg_exchange.set_transport(send_callback=None, receive_callback=None)
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
                # Try to get corresponding stream socket
                cl_conn = self._active_client.get_connection()
                if cl_conn is not None:
                    cl_stream = cl_conn.get_stream(self.stream_type)

                    if cl_stream.get_writer() is None:  # We avoid sending if no writer is available
                        self._logger.debug("No writer available for active client")
                        self._active_client = None
                        return

                    if cl_stream.get_reader() is None:  # We stop receiving if no reader is available
                        self._logger.debug("No reader available for active client")
                        await self.msg_exchange.set_transport(send_callback=cl_stream.get_writer_call(), receive_callback=None)
                        await self.msg_exchange.stop()
                        return

                    await self.msg_exchange.set_transport(
                        send_callback=cl_stream.get_writer_call(),
                        receive_callback=cl_stream.get_reader_call(),
                    )
                    # Start msg exchange listener (always runs for async dispatch)
                    await self.msg_exchange.start()
                else:
                    self._logger.debug(
                        f"No valid stream for active client {self._active_client.screen_position}")
                    await self.msg_exchange.set_transport(send_callback=None, receive_callback=None)
                    await self.msg_exchange.stop()

            else:
                await self.msg_exchange.set_transport(send_callback=None, receive_callback=None)
                await self.msg_exchange.stop()
        finally:
            self._clear_buffer()

    async def _core_sender(self):
        """
        Core async loop for handling stream sending with optimized batching.
        """
        while self._active:
            if self._active_client is not None and self._active_client.is_connected:
                try:
                    screen = self._active_client.get_screen_position() # Before the first await to avoid missing active client
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
                except AttributeError:
                    # Active client became None during await
                    self._active_client = None
                except (BrokenPipeError, ConnectionResetError) as e:
                    self._logger.warning(f"Connection error -> {e}")
                    # Set active client to None on connection errors
                    self._active_client = None
                except Exception as e:
                    self._logger.error(f"Error in core loop -> {e}")
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
        self.handler_id = handler_id if handler_id else f"{self.__class__.__name__}-{self.stream_type}"
        self.source = source

        # Create a MessageExchange object
        self.msg_exchange = MessageExchange(
            conf=MessageExchangeConfig(auto_dispatch=True),
            id=self.handler_id
        )

        self._logger = get_logger(self.handler_id)

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

    async def _on_client_disconnected(self, data: Optional[ClientDisconnectedEvent]):
        """
        Async event handler for when a client becomes inactive.
        """
        if data is None:
            return

        client_screen = data.client_screen
        if self._active_client is not None and self._active_client.screen_position == client_screen:
            self._active_client = None
            #self.logger.debug(f"Active client disconnected {client_screen}")
            await self.msg_exchange.set_transport(send_callback=None, receive_callback=None)

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
                # Try to get corresponding stream socket
                cl_conn = self._active_client.get_connection()
                if cl_conn is not None:
                    cl_stream = cl_conn.get_stream(self.stream_type)

                    if cl_stream.get_writer() is None:  # We avoid sending if no writer is available
                        self._logger.debug("No writer available for active client")
                        self._active_client = None
                        return

                    if cl_stream.get_reader() is None:  # We stop receiving if no reader is available
                        self._logger.debug("No reader available for active client")
                        await self.msg_exchange.set_transport(send_callback=cl_stream.get_writer_call(), receive_callback=None)
                        await self.msg_exchange.stop()
                        return

                    await self.msg_exchange.set_transport(
                        send_callback=cl_stream.get_writer_call(),
                        receive_callback=cl_stream.get_reader_call(),
                    )
                    # Start msg exchange listener (always runs for async dispatch)
                    await self.msg_exchange.start()
                else:
                    self._logger.debug(
                        f"No valid stream for active client {self._active_client.screen_position}")
                    await self.msg_exchange.set_transport(send_callback=None, receive_callback=None)
                    await self.msg_exchange.stop()

            else:
                await self.msg_exchange.set_transport(send_callback=None, receive_callback=None)
                await self.msg_exchange.stop()
        finally:
            self._clear_buffer()

    async def _core_sender(self):
        """
        Core async loop for handling stream sending with optimized batching.
        """
        while self._active:
            if self._active_client is not None and self._active_client.is_connected:
                try:
                    screen = self._active_client.get_screen_position()  # Before the first await to avoid missing active client
                    # Process sending queued data
                    data = await self._send_queue.get()
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
                except AttributeError:
                    # Active client became None during await
                    self._logger.debug(
                        f"No valid stream for active client {self._active_client.screen_position}")
                    pass
                except (BrokenPipeError, ConnectionResetError) as e:
                    self._logger.warning(f"Connection error -> {e}")
                    # Set active client to None on connection errors
                    self._active_client = None
                except Exception as e:
                    self._logger.error(f"Error in core loop -> {e}")
                    await asyncio.sleep(self._waiting_time)
            else:
                await asyncio.sleep(self._waiting_time)

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
        It initializes an instance responsible for managing bidirectional communication
        across multiple clients for a specified stream type. This involves setting up message
        exchange mechanisms, logging, and event subscriptions to handle client connections
        and active screen changes.

        Attributes:
            _active_client (Optional[Client]): The current active client in
                the streaming session. Defaults to None.
            handler_id (str): Unique identifier for the handler, generated if
                not provided.
            source (str): Source identifier for the handler. Defaults to
                'server'.
            msg_exchange (MessageExchange): Handles message dispatching and
                communication related to the handler.
            _clients_connected (int): Tracks the number of connected clients.
            _logger (Logger): Logger instance tied to the handler for logging
                events and activities.

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
        """
        super().__init__(stream_type=stream_type, clients=clients, event_bus=event_bus, sender=True)

        self._active_client = None
        self.handler_id = handler_id if handler_id else f"{self.__class__.__name__}-{self.stream_type}"
        self.source = source

        self.msg_exchange = MessageExchange(
            conf=MessageExchangeConfig(auto_dispatch=True, multicast=True),
            id=self.handler_id
        )

        self._clients_connected = 0

        self._logger = get_logger(self.handler_id)

        # Subscribe with async callbacks
        self.event_bus.subscribe(event_type=EventType.CLIENT_CONNECTED, callback=self._on_client_connected)
        self.event_bus.subscribe(event_type=EventType.CLIENT_DISCONNECTED, callback=self._on_client_disconnected)
        # We don't need active screen changed for multicast
        #self.event_bus.subscribe(event_type=EventType.ACTIVE_SCREEN_CHANGED, callback=self._on_active_screen_changed)

    def register_receive_callback(self, receive_callback, message_type: str):
        """
        Register a callback function for receiving messages of a specific type.
        This is delegated to MessageExchange which handles async dispatch automatically.
        """
        self.msg_exchange.register_handler(message_type, receive_callback)

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
                    await self.msg_exchange.set_transport(
                        send_callback=cl_stream.get_writer_call(),
                        receive_callback=cl_stream.get_reader_call(),
                        tr_id=client_screen
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
            #self.logger.log(f"Active client disconnected {client_screen}", Logger.INFO)
            await self.msg_exchange.set_transport(send_callback=None,
                                                  receive_callback=None,
                                                  tr_id=client_screen)
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
                # Try to get corresponding stream socket
                cl_conn = self._active_client.get_connection()
                if cl_conn is not None:
                    cl_stream = cl_conn.get_stream(self.stream_type)

                    if cl_stream.get_writer() is None:  # We avoid sending if no writer is available
                        self._logger.debug("No writer available for active client")
                        self._active_client = None
                        return

                    if cl_stream.get_reader() is None:  # We stop receiving if no reader is available
                        self._logger.debug("No reader available for active client")
                        await self.msg_exchange.set_transport(send_callback=cl_stream.get_writer_call(),
                                                              receive_callback=None,
                                                              tr_id=active_screen)
                        await self.msg_exchange.stop()
                        return

                    await self.msg_exchange.set_transport(
                        send_callback=cl_stream.get_writer_call(),
                        receive_callback=cl_stream.get_reader_call(),
                        tr_id=active_screen
                    )
                    # Start msg exchange listener (always runs for async dispatch)
                    await self.msg_exchange.start()
                else:
                    self._logger.debug(
                        f"No valid stream for active client {self._active_client.screen_position}")
                    await self.msg_exchange.set_transport(send_callback=None, receive_callback=None, tr_id=active_screen)
                    await self.msg_exchange.stop()

            else:
                await self.msg_exchange.set_transport(send_callback=None, receive_callback=None, tr_id=active_screen)
                await self.msg_exchange.stop()
        finally:
            self._clear_buffer()

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

                    await self.msg_exchange.send_stream_type_message(
                        stream_type=self.stream_type,
                        source=self.source,
                        #target=client.screen_position, # Let the multicast message exchange handle all targets
                        **data
                    )

                except asyncio.TimeoutError:
                    await asyncio.sleep(self._waiting_time)
                    continue
                except AttributeError:
                    # Active client became None during await
                    self._active_client = None
                except (BrokenPipeError, ConnectionResetError) as e:
                    self._logger.warning(f"Connection error -> {e}")
                    # Set active client to None on connection errors
                    self._active_client = None
                except Exception as e:
                    self._logger.error(f"Error in core loop -> {e}")
                    await asyncio.sleep(self._waiting_time)
            else:
                await asyncio.sleep(self._waiting_time)

    async def stop(self):
        await super().stop()
        await self.msg_exchange.stop()

    async def start(self) -> bool:
        st = await super().start()
        if self._clients_connected > 0:
            await self.msg_exchange.start()
        return st


