
#  Perpatua - open-source and cross-platform KVM software.
#  Copyright (c) 2026 Federico Izzi.
#
#  This program is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program.  If not, see <https://www.gnu.org/licenses/>.
#

import asyncio
from typing import Any, Optional

from event import (
    EventType,
    ActiveScreenChangedEvent,
    ClientStreamReconnectedEvent,
    ClientDisconnectedEvent,
    ClientConnectedEvent,
    ClientActiveEvent,
)
from event.bus import EventBus
from model.client import ClientsManager, ClientObj
from network.data import MissingTransportError
from network.data.exchange import MessageExchange, MessageExchangeConfig
from utils.logging import get_logger, Logger
from utils.metrics import MetricsCollector


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

    async def _configure_stream_transport_for_client(
        self,
        client: ClientObj,
        stream_type: int,
        msg_exchange: MessageExchange,
        transport_id: Optional[str],
    ) -> bool:
        """
        Configures the transport settings for a client stream and manages the
        message exchange based on the client's connection and stream state.

        This method verifies the validity of the client's connection and stream
        availability. It ensures that appropriate send and receive callbacks
        are set up for the message exchange, and manages the lifecycle of the
        message exchange process.

        Args:
            client: The client object whose stream transport needs to be configured.
            stream_type: The type of the stream (represented as an integer)
                to be checked for the client.
            msg_exchange: The message exchange object responsible for managing
                communication with the client.
            transport_id: For multicast configurations, a transport ID must be provided.

        Returns:
            bool: True if the transport was successfully configured, False otherwise.
        """
        cl_conn = client.get_connection()
        if cl_conn is None:
            self._logger.debug(
                f"No valid stream for active client {client.screen_position}"
            )
            await msg_exchange.set_transport(
                send_callback=None,
                receive_callback=None,
                tr_id=transport_id,
            )
            await msg_exchange.stop()
            return False

        cl_stream = cl_conn.get_stream(stream_type)
        if cl_stream is None:
            await msg_exchange.set_transport(
                send_callback=None,
                receive_callback=None,
                tr_id=transport_id,
            )
            await msg_exchange.stop()
            await asyncio.sleep(0)
            return False

        if cl_stream.get_writer() is None:
            self._logger.debug("No writer available for active client")
            return False

        if cl_stream.get_reader() is None:
            self._logger.debug("No reader available for active client")
            await msg_exchange.set_transport(
                send_callback=cl_stream.get_writer_call(),
                receive_callback=None,
                tr_id=transport_id,
            )
            await msg_exchange.stop()
            return False

        await msg_exchange.set_transport(
            send_callback=cl_stream.get_writer_call(),
            receive_callback=cl_stream.get_reader_call(),
            tr_id=transport_id,
        )
        await msg_exchange.start()
        return True


class _ServerStreamHandler(StreamHandler):
    """
    Base class for server-side stream handlers.
    """

    def __init__(
        self,
        stream_type: int,
        clients: ClientsManager,
        event_bus: EventBus,
        handler_id: Optional[str] = None,
        source: str = "server",
        sender: bool = True,
        metrics_collector: Optional[MetricsCollector] = None,
        buffer_size: int = 1000,
    ):
        """
        Initializes and configures an instance responsible for managing the interaction between
        a specified stream type, connected clients, and event-driven communication. This
        initialization involves creating necessary message exchange components, setting up logging,
        and subscribing to specific events.

        Args:
            stream_type (int): Represents the type of the stream being managed.
            clients (ClientsManager): Instance managing client connections and their state.
            event_bus (EventBus): Centralized event bus for subscribing and broadcasting events.
            handler_id (Optional[str]): Unique identifier for the handler; defaults to a derived
                value based on class name and stream type if not provided.
            source (str): Identifier indicating the origin of the handler, defaulting to "server".
            sender (bool): Determines whether the handler acts as a sender, default is True.
            metrics_collector (Optional[MetricsCollector]): Optional metrics collector for
                gathering performance data.
            buffer_size (int): Size of the internal buffer for managing outgoing messages.

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
                - CLIENT_STREAM_RECONNECTED
        """
        super().__init__(
            stream_type=stream_type,
            clients=clients,
            event_bus=event_bus,
            sender=sender,
            buffer_size=buffer_size,
        )

        self._active_client: Optional[ClientObj] = None
        self.handler_id = (
            handler_id
            if handler_id
            else f"{self.__class__.__name__}-{self.stream_type}"
        )
        self._logger = get_logger(self.handler_id)

        self.source = source

        self.msg_exchange = self._build_exchange(metrics_collector=metrics_collector)

        self._bus_subscribe()

    def _bus_subscribe(self):
        """
        Subscribe to relevant events on the event bus.

        Redefine in subclasses if needed.
        """
        self.event_bus.subscribe(
            event_type=EventType.ACTIVE_SCREEN_CHANGED,
            callback=self._on_active_screen_changed,
        )
        self.event_bus.subscribe(
            event_type=EventType.CLIENT_DISCONNECTED,
            callback=self._on_client_disconnected,
        )
        self.event_bus.subscribe(
            event_type=EventType.CLIENT_STREAM_RECONNECTED,
            callback=self._on_streams_reconnected,
        )

        # self.event_bus.subscribe(
        #     event_type=EventType.SCREEN_CHANGE_GUARD,
        #     callback=self._on_active_screen_change_guard,
        # )

    def register_receive_callback(self, receive_callback, message_type: str):
        """
        Register a callback function for receiving messages of a specific type.
        This is delegated to MessageExchange which handles async dispatch automatically.
        """
        self.msg_exchange.register_handler(message_type, receive_callback)

    def _build_exchange(
        self, metrics_collector: Optional[MetricsCollector]
    ) -> MessageExchange:
        """
        Create a MessageExchange instance for the stream handler.
        """
        return MessageExchange(
            conf=MessageExchangeConfig(auto_dispatch=True),
            id=self.handler_id,
            metrics_collector=metrics_collector,
        )

    async def _on_active_screen_change_guard(
        self, data: Optional[ActiveScreenChangedEvent]
    ):
        """
        Async event handler for the screen change guard.
        """
        # This can avoid sending data (to wrong target) during screen change transitions.
        # By default, we invalidate the active client on screen change
        self._active_client = None

    async def _on_active_screen_changed(self, data: Optional[ActiveScreenChangedEvent]):
        """
        Async event handler for when the active screen changes.
        """
        pass

    async def _on_streams_reconnected(
        self, data: Optional[ClientStreamReconnectedEvent]
    ):
        """
        Async event handler for when a client stream reconnects.
        """
        pass

    async def _on_client_disconnected(self, data: Optional[ClientDisconnectedEvent]):
        """
        Async event handler for when a client disconnect.
        """
        pass

    async def _on_client_connected(self, data: Optional[ClientConnectedEvent]):
        """
        Async event handler for when a client connects.
        """
        pass

    async def _send_logic(self):
        """
        Core sending logic for the stream handler.

        Can be overridden in subclasses for custom behavior.
        """
        if self._active_client is None:
            raise AttributeError("Client is not active")

        screen = (
            self._active_client.get_screen_position()
        )  # Before the first await to avoid missing active client
        data = await self._send_queue.get()
        # Retake screen in case active client changed during await
        # Only a switch to None can occur here,
        # because we have screen A -> None -> screen B,
        screen = (
            self._active_client.get_screen_position()
            if self._active_client is not None
            else screen
        )
        if not isinstance(data, dict) and hasattr(data, "to_dict"):
            data = data.to_dict()
        await self.msg_exchange.send_stream_type_message(
            stream_type=self.stream_type,
            source=self.source,
            target=screen,
            **data,
        )

    def _send_clause(self) -> bool:
        """
        Determine if sending is allowed.

        Must be implemented in subclasses.

        Returns:
            True if sending is allowed, False otherwise.
        """
        raise NotImplementedError("_send_clause must be implemented in subclasses.")

    async def _core_sender(self):
        while self._active:
            if self._send_clause():
                try:
                    await self._send_logic()
                except asyncio.TimeoutError:
                    await asyncio.sleep(self._waiting_time)
                    continue
                except AttributeError:
                    # Active client became None during await
                    self._active_client = None
                    await asyncio.sleep(0)  # yield control
                except (BrokenPipeError, ConnectionResetError) as e:
                    self._logger.warning(f"Connection error -> {e}")
                    # Set active client to None on connection errors
                    self._active_client = None
                    await asyncio.sleep(0)  # yield control
                except MissingTransportError:
                    self._logger.warning("Missing transport")
                    await asyncio.sleep(0)  # yield control
                except RuntimeError as e:
                    # uv/winloop runtime error on closed tcp transport
                    if "closed=True" in str(e):
                        self._logger.warning("Transport closed")
                        await asyncio.sleep(0)  # yield control
                    else:
                        self._logger.error(f"Runtime error in core loop -> {e}")
                        await asyncio.sleep(self._waiting_time)
                    self._active_client = None
                except Exception as e:
                    self._logger.error(f"Error in core loop -> {e}")
                    await asyncio.sleep(self._waiting_time)
            else:
                await asyncio.sleep(self._waiting_time)

    async def stop(self):
        await super().stop()
        await self.msg_exchange.stop()


class _ClientStreamHandler(StreamHandler):
    """
    Base class for client stream handlers.
    """

    def __init__(
        self,
        stream_type: int,
        clients: ClientsManager,
        event_bus: EventBus,
        handler_id: Optional[str] = None,
        sender: bool = True,
        active_only: bool = False,
        metrics_collector: Optional[MetricsCollector] = None,
        buffer_size: int = 1000,
    ):
        """
        It handles stream management and event subscription for the client.

        Args:
            stream_type (int): Identifier for the type of stream used.
            clients (ClientsManager): Object responsible for managing client connections (just one in client mode).
            event_bus (EventBus): A bus that broadcasts events and enables subscriptions to events.
            handler_id (Optional[str]): Unique identifier for the handler, autogenerated if not provided.
            sender (bool): Indicates if the instance should initiate sending operations. Defaults to True.
            active_only (bool): Determines if only an active client should be managed. Defaults to False.
            metrics_collector (Optional[MetricsCollector]): Collector for gathering metrics data. Defaults to None.
            buffer_size (int): Size of the internal buffer for sending messages. Defaults to 1000.
        """
        super().__init__(
            stream_type=stream_type,
            clients=clients,
            event_bus=event_bus,
            sender=sender,
            buffer_size=buffer_size,
        )

        self._is_active = False  # Track if current client is active

        self.handler_id = (
            handler_id
            if handler_id
            else f"{self.__class__.__name__}-{self.stream_type}"
        )
        self._active_only = active_only
        self._main_client: Optional[ClientObj] = self.clients.get_client()

        self._logger = get_logger(self.handler_id)

        self.msg_exchange = self._build_exchange(metrics_collector=metrics_collector)

        self._bus_subscribe()

    def _build_exchange(
        self, metrics_collector: Optional[MetricsCollector]
    ) -> MessageExchange:
        """
        Create a MessageExchange instance for the stream handler.
        """
        return MessageExchange(
            conf=MessageExchangeConfig(auto_dispatch=True),
            id=self.handler_id,
            metrics_collector=metrics_collector,
        )

    def _bus_subscribe(self):
        """
        Subscribe to relevant events on the event bus.

        Redefine in subclasses if needed.
        """
        self.event_bus.subscribe(
            event_type=EventType.CLIENT_ACTIVE, callback=self._on_client_active
        )
        self.event_bus.subscribe(
            event_type=EventType.CLIENT_INACTIVE, callback=self._on_client_inactive
        )
        self.event_bus.subscribe(
            event_type=EventType.CLIENT_STREAM_RECONNECTED,
            callback=self._on_streams_reconnected,
        )

    async def _on_client_active(self, data: Optional[ClientActiveEvent]):
        """
        Async event handler for when a client becomes active.
        Redefine in subclasses.
        """
        pass

    async def _on_client_inactive(self, data: Optional[ClientActiveEvent]):
        """
        Async event handler for when a client becomes inactive.
        Redefine in subclasses.
        """
        pass

    async def _on_streams_reconnected(
        self, data: Optional[ClientStreamReconnectedEvent]
    ):
        """
        Async event handler for when a client's streams are reconnected.
        Redefine in subclasses.
        """
        pass

    async def _send_logic(self):
        """
        Core sending logic for the stream handler.

        Can be overridden in subclasses for custom behavior.
        """
        screen = ""
        if self._main_client is not None:
            screen = self._main_client.get_screen_position()

        # Get data from queue
        data = await self._send_queue.get()
        if not isinstance(data, dict) and hasattr(data, "to_dict"):
            data = data.to_dict()
        await self.msg_exchange.send_stream_type_message(
            stream_type=self.stream_type,
            source=screen,
            target="server",
            **data,
        )

    def _send_clause(self) -> bool:
        """
        Determine if sending is allowed.

        Must be implemented in subclasses.

        Returns:
            True if sending is allowed, False otherwise.
        """
        return self._active_only and not self._is_active

    async def _handle_disconnection(self):
        """
        Handle disconnection logic for the client stream handler.
        """
        self._is_active = False
        await self.msg_exchange.stop()
        self._clear_buffer()
        # try to close client stream
        if self._main_client is not None:
            cl_conn = self._main_client.get_connection()
            if cl_conn is not None:
                cl_stream = cl_conn.get_stream(self.stream_type)
                if cl_stream is not None:
                    await cl_stream.close()

    async def _core_sender(self):
        while self._active:
            if self._send_clause():
                await asyncio.sleep(self._waiting_time)
                continue

            try:
                await self._send_logic()
            except asyncio.TimeoutError:
                await asyncio.sleep(self._waiting_time)
                continue
            except MissingTransportError:
                self._logger.warning("Missing transport")
                await asyncio.sleep(0)  # yield control
            except (ConnectionResetError, BrokenPipeError) as e:
                self._logger.error(f"Connection error -> {e}")
                # if connection lost error, close the stream
                if "connection lost" in str(e).lower() or self._active_only:
                    await self._handle_disconnection()
                await asyncio.sleep(self._waiting_time)
            except RuntimeError as e:
                # uv/winloop runtime error on closed tcp transport
                if "closed=True" in str(e):
                    self._logger.warning("Transport closed")
                    await asyncio.sleep(0)  # yield control
                else:
                    self._logger.error(f"Runtime error in core loop -> {e}")
                    await asyncio.sleep(self._waiting_time)
                if self._active_only:
                    self._is_active = False
                    await self.msg_exchange.stop()
                    self._clear_buffer()
            except Exception as e:
                self._logger.error(f"Error in core loop -> {e}")
                await asyncio.sleep(self._waiting_time)

    def register_receive_callback(self, receive_callback, message_type: str):
        """
        Register a callback function for receiving messages of a specific type.
        This is delegated to MessageExchange which handles async dispatch automatically.
        """
        self.msg_exchange.register_handler(message_type, receive_callback)

    async def stop(self):
        await super().stop()
        await self.msg_exchange.stop()
