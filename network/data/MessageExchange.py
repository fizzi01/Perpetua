"""
Layer responsible for handling message exchanges between network nodes, using protocol
"""
import threading
from time import sleep, time
from typing import Callable, Dict, Optional, Any, List
from dataclasses import dataclass

from config import ApplicationConfig
from utils.logging import Logger
from network.protocol.message import ProtocolMessage, MessageBuilder
from network.protocol.ordering import OrderedMessageProcessor

from network.stream import StreamType


@dataclass
class MessageExchangeConfig:
    """Configuration for MessageExchange layer."""
    max_delay_tolerance: float = ApplicationConfig.max_delay_tolerance
    max_chunk_size: int = ApplicationConfig.max_chunk_size  # bytes
    enable_ordering: bool = True
    auto_chunk: bool = ApplicationConfig.auto_chunk
    parallel_processors: int = ApplicationConfig.parallel_processors


class MessageExchange:
    """
    High-level abstraction layer for message exchange between nodes.
    Handles protocol details, chunking, ordering, and callbacks.
    """

    def __init__(self, conf: MessageExchangeConfig = None):
        """
        Initialize MessageExchange layer.

        Args:
            conf: Configuration object for the exchange layer
        """
        self.config = conf or MessageExchangeConfig()
        self.builder = MessageBuilder()

        # Message handlers registry
        self._handlers: Dict[str, Callable[[ProtocolMessage], None]] = {}

        # Ordered processor if enabled
        self.processor: Optional[OrderedMessageProcessor] = None
        if self.config.enable_ordering:
            self.processor = OrderedMessageProcessor(
                process_callback=self._dispatch_message,
                max_delay_tolerance=self.config.max_delay_tolerance,
                parallel_processors=self.config.parallel_processors,
            )
            self.processor.start()

        # Chunk reassembly buffer
        self._chunk_buffer: Dict[str, list] = {}
        self._buffer_lock = threading.Lock()

        # Transport layer callbacks
        self._send_callback: Optional[Callable[[bytes], None]] = None
        self._receive_callback: Optional[Callable[[int], bytes]] = None

        self.logger = Logger.get_instance()

    def set_transport(self, send_callback: Optional[Callable[[bytes], None]] = None, receive_callback: Optional[Callable[[int], bytes]] = None):
        """
        Set the transport layer callback for sending messages.

        Args:
            send_callback: Function that sends bytes over the network
        """
        self._send_callback = send_callback
        self._receive_callback = receive_callback

    def register_handler(self, message_type: str, handler: Callable[[ProtocolMessage], None]):
        """
        Register a handler for a specific message type.

        Args:
            message_type: Type of message to handle (mouse, keyboard, etc.)
            handler: Callback function to process the message
        """
        self._handlers[message_type] = handler

    def send_mouse_data(self, x: float, y: float,event: str, dx: float, dy: float,
                        is_pressed: bool = False, source: str = None, target: str = None, **kwargs):
        """Send a mouse event message."""
        message = self.builder.create_mouse_message(x, y, dx, dy, event, is_pressed, source=source, target=target, **kwargs)
        self._send_message(message)

    def send_keyboard_data(self, key: str, event: str, source: str = None, target: str = None):
        """Send a keyboard event message."""
        message = self.builder.create_keyboard_message(key, event,source=source, target=target)
        self._send_message(message)

    def send_clipboard_data(self, content: str, content_type: str = "text",source: str = None, target: str = None):
        """Send clipboard data message."""
        message = self.builder.create_clipboard_message(content, content_type,source=source, target=target)
        self._send_message(message)

    def send_file_data(self, command: str, data: Dict[str, Any],source: str = None, target: str = None):
        """Send file transfer message."""
        message = self.builder.create_file_message(command, data,source=source, target=target)
        self._send_message(message)

    def send_screen_command(self, command: str, data: Dict[str, Any] = None,source: str = None, target: str = None):
        """Send screen command message."""
        message = self.builder.create_screen_message(command, data,source=source, target=target)
        self._send_message(message)

    def send_command_message(self, command: str, params: Dict[str, Any] = None, source: str = None, target: str = None):
        """Send a generic command message."""
        message = self.builder.create_command_message(command, params, source=source, target=target)
        self._send_message(message)

    def send_handshake_message(self, client_name: str = None, screen_resolution: str = None,
                                 screen_position: str = None, additional_params: Dict[str, Any] = None,
                                 ack: bool = True, ssl: bool = False, streams: List[int] = None,
                                 source: str = None, target: str = None):
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
            target=target
        )
        self._send_message(message)

    def send_stream_type_message(self, stream_type: int, source: str = None, target: str = None, **kwargs):
        """Send stream type message."""

        match stream_type:
            case StreamType.MOUSE:
                self.send_mouse_data(source=source, target=target, **kwargs)
                return
            case StreamType.KEYBOARD:
                self.send_keyboard_data(source=source, target=target, **kwargs)
                return
            case StreamType.CLIPBOARD:
                self.send_clipboard_data(source=source, target=target, **kwargs)
                return
            case StreamType.FILE:
                self.send_file_data(source=source, target=target, **kwargs)
                return
            case StreamType.COMMAND:
                self.send_command_message(source=source, target=target, **kwargs)
                return
            case _:
                self.logger.log(f"Unknown stream type: {stream_type}", Logger.ERROR)
                return

    def send_custom_message(self, message_type: str, payload: Dict[str, Any],source: str = None, target: str = None):
        """Send a custom message with arbitrary payload."""
        # noinspection PyProtectedMember
        message = ProtocolMessage(
            message_type=message_type,
            timestamp=self.builder._next_sequence_id(),
            sequence_id=self.builder._next_sequence_id(),
            payload=payload,
            source=source,
            target=target
        )
        self._send_message(message)

    def _send_message(self, message: ProtocolMessage):
        """
        Internal method to send a message through the transport layer.
        Handles automatic chunking if enabled.
        """
        if not self._send_callback:
            raise RuntimeError("Transport layer not configured. Call set_transport() first.")

        # Check if chunking is needed
        if self.config.auto_chunk and message.get_serialized_size() > self.config.max_chunk_size:
            chs = self.builder.create_chunked_message(message, self.config.max_chunk_size)
            for ch in chs:
                data = ch.to_bytes()
                self._send_callback(data)
        else:
            data = message.to_bytes()
            self._send_callback(data)

    def receive_message(self, instant: bool = False) -> Optional[ProtocolMessage]:
        """
        Receive a message from the transport layer socket.

        Args:
            instant: If True, process message immediately without ordering returning it
        """
        try:
            data = self._receive_callback(self.config.max_chunk_size)
            if data:
                return self._receive_data(data, instant=instant)
            return None
        except Exception as e:
            self.logger.log(f"Error receiving message: {e}", Logger.ERROR)
            return None

    def _receive_data(self, data: bytes, instant: bool = False):
        """
        Receive raw bytes from transport layer and process them.

        Args:
            data: Raw bytes received from network
        """
        try:
            message = ProtocolMessage.from_bytes(data)

            # Handle chunked messages
            if message.is_chunk and not instant:
                reconstructed = self._handle_chunk(message)
                if reconstructed:
                    self._process_received_message(reconstructed)
                    return None
                return None
            else:
                return self._process_received_message(message, instant=instant)

        except Exception as e:
            self.logger.log(f"Error processing received data: {e}", Logger.ERROR)
            return None

    def _handle_chunk(self, chunk: ProtocolMessage) -> Optional[ProtocolMessage]:
        """
        Handle a chunked message and reassemble when complete.

        Returns:
            Reconstructed message if all chunks received, None otherwise
        """
        with self._buffer_lock:
            message_id = chunk.message_id

            if message_id not in self._chunk_buffer:
                self._chunk_buffer[message_id] = [None] * chunk.total_chunks

            self._chunk_buffer[message_id][chunk.chunk_index] = chunk

            # Check if all chunks received
            if all(c is not None for c in self._chunk_buffer[message_id]):
                chunks = self._chunk_buffer.pop(message_id)
                return self.builder.reconstruct_from_chunks(chunks)

        return None

    def _process_received_message(self, message: ProtocolMessage, instant: bool = False):
        """Process a received message (after reassembly if chunked)."""
        if self.config.enable_ordering and not instant:
            # Add to ordered processor
            self.processor.add_message(message)
            return None
        elif instant:
            # Immediate processing
            return message
        else:
            # Process immediately
            self._dispatch_message(message)
            return None

    def _dispatch_message(self, message: ProtocolMessage):
        """Dispatch message to registered handler."""
        handler = self._handlers.get(message.message_type)
        if handler:
            try:
                handler(message)
            except Exception as e:
                self.logger.log(f"Error in message handler for {message.message_type}: {e}", Logger.ERROR)
        else:
            self.logger.log(f"No handler registered for message type: {message.message_type}", Logger.ERROR)

    def stop(self):
        """Cleanup and shutdown the message exchange layer."""
        if self.processor:
            self.processor.stop()

        with self._buffer_lock:
            self._chunk_buffer.clear()


# Esempio di utilizzo
if __name__ == "__main__":
    # Configurazione
    config = MessageExchangeConfig(
        max_delay_tolerance=0.1,
        max_chunk_size=1024,
        enable_ordering=True,
        parallel_processors=2,
    )

    # Inizializza exchange
    exchange = MessageExchange(config)

    # Simula transport layer
    def mock_send(data: bytes):
        print(f"Sending {len(data)} bytes")

    exchange.set_transport(mock_send)

    # Registra handlers
    def handle_mouse(msg: ProtocolMessage):
        print(f"Mouse event: {msg.payload}")

    def handle_keyboard(msg: ProtocolMessage):
        print(f"Keyboard event: {msg.payload}")

    def handle_clipboard(msg: ProtocolMessage):
        print(f"Clipboard event: {msg.payload}, length: {len(msg.payload.get('content', ''))}")

    exchange.register_handler("mouse", handle_mouse)
    exchange.register_handler("keyboard", handle_keyboard)
    exchange.register_handler("clipboard", handle_clipboard)

    # Invia messaggi
    exchange.send_mouse_data(100, 200, "click", is_pressed=True)
    exchange.send_keyboard_data("A", "press")

    # Simula ricezione dati
    raw_mouse_data = exchange.builder.create_mouse_message(150, 250, "move").to_bytes()
    exchange._receive_data(raw_mouse_data)

    raw_keyboard_data = exchange.builder.create_keyboard_message("B", "release").to_bytes()
    exchange._receive_data(raw_keyboard_data)

    # Test clipboard with more than max_chunk_size to trigger chunking
    large_content = "A" * 5000  # 5000 bytes of data
    exchange.send_clipboard_data(large_content)

    # Simula ricezione dei chunk
    chunks = exchange.builder.create_chunked_message(
        exchange.builder.create_clipboard_message(large_content),
        config.max_chunk_size,
    )
    for chunk in chunks:
        exchange._receive_data(chunk.to_bytes())

    # Test out of order delivery
    out_of_order_chunks = exchange.builder.create_chunked_message(
        exchange.builder.create_clipboard_message("Out of order test " * 300),
        config.max_chunk_size,
    )
    # Invia i chunk in ordine inverso
    for chunk in reversed(out_of_order_chunks):
        exchange._receive_data(chunk.to_bytes())

    # Test out of order with numbers from 1 to 1000
    out_of_order_numbers = exchange.builder.create_chunked_message(
        exchange.builder.create_clipboard_message("".join(str(i) + " " for i in range(1, 1001))),
        config.max_chunk_size,
    )

    # Invia i chunk in ordine inverso
    for chunk in reversed(out_of_order_numbers):
        exchange._receive_data(chunk.to_bytes())

    sleep(1)
    # Cleanup
    exchange.stop()