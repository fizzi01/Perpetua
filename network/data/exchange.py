"""
Layer responsible for handling message exchanges between network nodes, using protocol
"""

import asyncio
from dataclasses import dataclass
from time import time
from typing import Callable, Dict, Optional, Any, List

from config import ApplicationConfig
from network.data import MissingTransportError
from network.protocol.message import ProtocolMessage, MessageBuilder
from network.stream import StreamType
from utils.logging import Logger, get_logger


@dataclass
class MessageExchangeConfig:
    """Configuration for MessageExchange layer."""

    max_delay_tolerance: float = ApplicationConfig.max_delay_tolerance
    max_chunk_size: int = ApplicationConfig.max_chunk_size  # bytes
    auto_chunk: bool = ApplicationConfig.auto_chunk
    auto_dispatch: bool = True
    receive_buffer_size: int = 65536  # bytes for asyncio receive buffer
    multicast: bool = False  # Whether to use multicast transport


class MessageExchange:
    """
    High-level abstraction layer for message exchange between nodes.
    Handles protocol details, chunking, ordering, and callbacks using asyncio.
    """

    DEFAULT_TRANSPORT_ID = "default"

    def __init__(self, conf: Optional[MessageExchangeConfig] = None, id="default"):
        """
        Initialize MessageExchange layer.

        Args:
            conf: Configuration object for the exchange layer
        """
        self._id = id
        self.config = conf or MessageExchangeConfig()
        self.builder = MessageBuilder()

        # Message handlers registry
        self._handlers: Dict[str, Callable[[ProtocolMessage], Any]] = {}

        # Chunk reassembly buffer
        self._chunk_buffer: Dict[str, list[Optional[ProtocolMessage]]] = {}

        # Transport layer callbacks
        # We support multiple transports for multicast scenarios
        self._send_callbacks: Dict[str, Optional[Callable[[bytes], Any]]] = {}
        self._receive_callbacks: Dict[str, Optional[Callable[[int], Any]]] = {}

        # Asyncio components
        self._receive_task: Optional[asyncio.Task] = None
        self._message_queue: Optional[asyncio.Queue] = None
        self._running = False

        self._missed_data = 0

        self._lock = asyncio.Lock()

        self._logger = get_logger(f"{self.__class__.__name__}({self._id})")

    async def start(self):
        """Start asyncio task for incoming messages."""
        if self._running:
            return

        # if not self._receive_callback:
        #     raise RuntimeError("Transport layer not configured. Call set_transport() first.")

        self._running = True
        self._message_queue = asyncio.Queue(maxsize=10000)
        self._receive_task = asyncio.create_task(self._receive_loop())

        self._logger.debug("Started")

    async def stop(self):
        """Cleanup and shutdown the message exchange layer."""
        self._running = False

        if self._receive_task:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass

        self._chunk_buffer.clear()
        self._logger.debug("Stopped")

    async def _receive_loop(self):
        """
        Core loop for receiving and processing incoming messages.
        Handles chunk reassembly and dispatching to registered handlers.
        """
        persistent_buffer = bytearray()
        prefix_len = ProtocolMessage.prefix_lenght
        max_msg_size = self.config.max_chunk_size * 100

        while self._running:
            for (
                tr_id,
                receive_callback,
            ) in self._receive_callbacks.items():  # Round-robin
                if receive_callback is None:
                    await asyncio.sleep(0)
                    continue

                try:
                    # Ricevi nuovi dati in modo non bloccante
                    async with self._lock:
                        new_data = await receive_callback(
                            self.config.receive_buffer_size
                        )
                    if not new_data:
                        await asyncio.sleep(0)  # Breve pausa per evitare busy waiting
                        continue

                    persistent_buffer.extend(new_data)
                    buffer_len = len(persistent_buffer)
                    offset = 0

                    # Processa tutti i messaggi completi nel buffer
                    while offset + prefix_len <= buffer_len:
                        try:
                            # Verifica marker "PY" prima di leggere il prefisso
                            if persistent_buffer[offset + 4 : offset + 6] != b"PY":
                                # Cerca il prossimo marker valido
                                next_marker = persistent_buffer.find(b"PY", offset + 1)
                                if next_marker == -1 or next_marker < 4:
                                    # Nessun marker trovato, mantieni ultimi 5 byte
                                    persistent_buffer = (
                                        persistent_buffer[-5:]
                                        if buffer_len > 5
                                        else bytearray()
                                    )
                                    await asyncio.sleep(0)
                                    break
                                offset = next_marker - 4
                                continue

                            # Leggi lunghezza messaggio
                            msg_length = ProtocolMessage.read_lenght_prefix(
                                bytes(persistent_buffer[offset : offset + prefix_len])
                            )

                            if msg_length > max_msg_size:
                                # Messaggio troppo grande, cerca prossimo marker
                                offset += 1
                                # await asyncio.sleep(0)
                                continue

                            total_length = prefix_len + msg_length

                            # Verifica se abbiamo il messaggio completo
                            if offset + total_length > buffer_len:
                                # Messaggio incompleto, mantieni da offset in poi
                                await asyncio.sleep(0)
                                break

                            # Estrai e processa il messaggio completo
                            message_data = bytes(
                                persistent_buffer[offset : offset + total_length]
                            )
                            message = ProtocolMessage.from_bytes(message_data)
                            message.timestamp = time()

                            if message.is_heartbeat():
                                # Ignora messaggi di heartbeat
                                offset += total_length
                                continue
                            # Gestione chunk/messaggio normale
                            if message.is_chunk:
                                reconstructed = await self._handle_chunk(message)
                                if reconstructed:
                                    if self.config.auto_dispatch:
                                        await self.dispatch_message(reconstructed)
                                    else:
                                        await self._message_queue.put(reconstructed)  # ty:ignore[possibly-missing-attribute]
                            else:
                                if self.config.auto_dispatch:
                                    await self.dispatch_message(message)
                                else:
                                    await self._message_queue.put(message)  # ty:ignore[possibly-missing-attribute]

                            offset += total_length
                            # await asyncio.sleep(0)

                        except ValueError:
                            # Prefisso invalido, avanza di 1 byte
                            offset += 1
                            continue

                    # Rimuovi dati processati dal buffer
                    if offset > 0:
                        persistent_buffer = persistent_buffer[offset:]

                except asyncio.CancelledError:
                    break
                except AttributeError:
                    # Transport layer disconnected
                    self._logger.log(
                        "Transport layer disconnected, stopping receive loop.",
                        Logger.WARNING,
                    )
                    self._running = False
                    break
                except RuntimeError as e:
                    self._logger.log(
                        f"Error in receive loop {self._id} -> {e}", Logger.CRITICAL
                    )
                    self._running = False
                    break
                except Exception as e:
                    # Catch broken pipe or connection reset errors
                    if isinstance(
                        e,
                        (
                            ConnectionResetError,
                            BrokenPipeError,
                            ConnectionError,
                            ConnectionAbortedError,
                        ),
                    ):
                        self._logger.log(
                            f"Connection error in receive loop -> {e}", Logger.ERROR
                        )
                        self._running = False
                        break
                    # Avoid infinite loop if no receive callback is set
                    if receive_callback is None:
                        self._logger.log(
                            "Receive callback is None, stopping receive loop.",
                            Logger.DEBUG,
                        )
                        self._running = False
                        break
                    import traceback

                    traceback.print_exc()
                    self._logger.log(
                        f"Error in receive loop {self._id} -> {e}", Logger.ERROR
                    )
                    await asyncio.sleep(0)
                    continue

    async def set_transport(
        self,
        send_callback: Optional[Callable] = None,
        receive_callback: Optional[Callable] = None,
        tr_id: Optional[str] = None,
    ):
        """
        Sets the transport callbacks for sending and receiving messages. If the
        configuration is for a single transport, the callbacks are set to a default
        transport ID. For multicast configurations, a transport ID must be provided.

        Parameters:
            send_callback: Optional[Callable]
                The callback function to handle sending messages.
            receive_callback: Optional[Callable]
                The callback function to handle receiving messages.
            tr_id: Optional[str]
                The transport ID associated with the callbacks, required in multicast
                configurations.
        """
        # async with self._lock: # Protect transport callbacks assignment
        if not self.config.multicast:  # Single transport
            self._send_callbacks[self.DEFAULT_TRANSPORT_ID] = send_callback
            self._receive_callbacks[self.DEFAULT_TRANSPORT_ID] = receive_callback
        else:
            if tr_id is None:
                raise ValueError(
                    "Transport ID must be provided for multicast configuration."
                )

            self._send_callbacks[tr_id] = send_callback
            self._receive_callbacks[tr_id] = receive_callback

    def register_handler(self, message_type: str, handler: Callable):
        """
        Register a handler for a specific message type.

        Args:
            message_type: Type of message to handle (mouse, keyboard, etc.)
            handler: Callback function to process the message
        """
        self._handlers[message_type] = handler

    async def send_mouse_data(
        self,
        x: float,
        y: float,
        event: str,
        dx: float,
        dy: float,
        is_pressed: bool = False,
        source: Optional[str] = None,
        target: Optional[str] = None,
        **kwargs,
    ):
        """Send a mouse event message."""
        message = self.builder.create_mouse_message(
            x, y, dx, dy, event, is_pressed, source=source, target=target, **kwargs
        )
        await self._send_message(message)

    async def send_keyboard_data(
        self,
        key: str,
        event: str,
        source: Optional[str] = None,
        target: Optional[str] = None,
    ):
        """Send a keyboard event message."""
        message = self.builder.create_keyboard_message(
            key, event, source=source, target=target
        )
        await self._send_message(message)

    async def send_clipboard_data(
        self,
        content: str,
        content_type: str = "text",
        source: Optional[str] = None,
        target: Optional[str] = None,
    ):
        """Send clipboard data message."""
        message = self.builder.create_clipboard_message(
            content, content_type, source=source, target=target
        )
        await self._send_message(message)

    async def send_file_data(
        self,
        command: str,
        data: Dict[str, Any],
        source: Optional[str] = None,
        target: Optional[str] = None,
    ):
        """Send file transfer message."""
        message = self.builder.create_file_message(
            command, data, source=source, target=target
        )
        await self._send_message(message)

    async def send_screen_command(
        self,
        command: str,
        data: Optional[Dict[str, Any]] = None,
        source: Optional[str] = None,
        target: Optional[str] = None,
    ):
        """Send screen command message."""
        message = self.builder.create_screen_message(
            command, data, source=source, target=target
        )
        await self._send_message(message)

    async def send_command_message(
        self,
        command: str,
        params: Optional[Dict[str, Any]] = None,
        source: Optional[str] = None,
        target: Optional[str] = None,
    ):
        """Send a generic command message."""
        message = self.builder.create_command_message(
            command, params, source=source, target=target
        )
        await self._send_message(message)

    async def send_handshake_message(
        self,
        client_name: Optional[str] = None,
        screen_resolution: Optional[str] = None,
        screen_position: Optional[str] = None,
        additional_params: Optional[Dict[str, Any]] = None,
        ack: bool = True,
        ssl: bool = False,
        streams: Optional[List[int]] = None,
        source: Optional[str] = None,
        target: Optional[str] = None,
    ):
        """Send handshake message."""
        message = self.builder.create_handshake_message(
            client_name,
            screen_resolution,
            screen_position,
            additional_params,
            ack=ack,
            ssl=ssl,
            streams=streams,
            source=source,
            target=target,
        )
        await self._send_message(message)

    async def send_stream_type_message(
        self,
        stream_type: int,
        source: Optional[str] = None,
        target: Optional[str] = None,
        **kwargs,
    ):
        """Send stream type message."""

        if stream_type == StreamType.MOUSE:
            await self.send_mouse_data(source=source, target=target, **kwargs)
            return
        elif stream_type == StreamType.KEYBOARD:
            await self.send_keyboard_data(source=source, target=target, **kwargs)
            return
        elif stream_type == StreamType.CLIPBOARD:
            await self.send_clipboard_data(source=source, target=target, **kwargs)
            return
        elif stream_type == StreamType.FILE:
            await self.send_file_data(source=source, target=target, **kwargs)
            return
        elif stream_type == StreamType.COMMAND:
            await self.send_command_message(source=source, target=target, **kwargs)
            return
        else:
            self._logger.log(f"Unknown stream type: {stream_type}", Logger.ERROR)
            return

    async def send_custom_message(
        self,
        message_type: str,
        payload: Dict[str, Any],
        source: Optional[str] = None,
        target: Optional[str] = None,
    ):
        """Send a custom message with arbitrary payload."""
        # noinspection PyProtectedMember
        message = ProtocolMessage(
            message_type=message_type,
            timestamp=self.builder._next_sequence_id(),
            sequence_id=self.builder._next_sequence_id(),
            payload=payload,
            source=source,
            target=target,
        )
        await self._send_message(message)

    async def _send_message(self, message: ProtocolMessage):
        """
        Internal method to send a message via the transport layer.
        If multicast is enabled, sends via all configured transports.
        Handles automatic chunking if enabled.
        """
        # Cycle through all send callbacks
        for tr_id, send_callback in self._send_callbacks.items():  # Round-robin
            if not send_callback:
                raise MissingTransportError(
                    "Transport layer not configured. Call set_transport() first."
                )

            # Set target if not already set
            message.target = message.target if message.target else tr_id

            # Check if chunking is needed
            if (
                self.config.auto_chunk
                and message.get_serialized_size() > self.config.max_chunk_size
            ):
                chs = self.builder.create_chunked_message(
                    message, self.config.max_chunk_size
                )
                for ch in chs:
                    data = ch.to_bytes()
                    if asyncio.iscoroutinefunction(send_callback):
                        await send_callback(data)
                    else:
                        send_callback(data)
            else:
                data = message.to_bytes()
                if asyncio.iscoroutinefunction(send_callback):
                    await send_callback(data)
                else:
                    send_callback(data)

    async def get_received_message(self) -> Optional[ProtocolMessage]:
        """
        Preleva un messaggio dalla coda di ricezione se disponibile.
        I chunk sono già gestiti nel processo di ricezione.

        Args:
            timeout: Tempo di attesa in secondi (0 = non bloccante)

        Returns:
            Messaggio ricevuto o None se la coda è vuota
        """
        if not self._message_queue:
            return None

        try:
            mex = await self._message_queue.get()
            return mex
        except (asyncio.TimeoutError, asyncio.QueueEmpty):
            return None

    async def _handle_chunk(self, chunk: ProtocolMessage) -> Optional[ProtocolMessage]:
        """
        Handle a chunked message and reassemble when complete.

        Returns:
            Reconstructed message if all chunks received, None otherwise
        """
        message_id = chunk.message_id
        if message_id is None:
            self._logger.debug(
                "Received chunk without message_id", data=chunk.to_dict()
            )
            return None

        if message_id not in self._chunk_buffer:
            self._chunk_buffer[message_id] = [None] * chunk.total_chunks  # ty:ignore[unsupported-operator]

        self._chunk_buffer[message_id][chunk.chunk_index] = chunk  # ty:ignore[invalid-assignment]

        # Check if all chunks received
        if all(c is not None for c in self._chunk_buffer[message_id]):
            chunks = self._chunk_buffer.pop(message_id)
            return self.builder.reconstruct_from_chunks(chunks)

        return None

    async def dispatch_message(self, message: ProtocolMessage):
        """Dispatch message to registered handler in modo asyncio."""
        handler = self._handlers.get(message.message_type)
        # Metriche di delay commentate per performance
        # curtime = time()
        # print(f"Pre-dispatch delay: {curtime - message.timestamp:.7f}s")
        if handler:
            try:
                # Se handler è async, await, altrimenti chiamalo normalmente
                if asyncio.iscoroutinefunction(handler):
                    await handler(message)
                else:
                    handler(message)
            except Exception as e:
                self._logger.log(
                    f"Error in message handler for {message.message_type}: {e}",
                    Logger.ERROR,
                )
        else:
            self._logger.log(
                f"No handler registered for message type: {message.message_type}",
                Logger.DEBUG,
            )
