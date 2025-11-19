"""
Layer responsible for handling message exchanges between network nodes, using protocol
"""

from time import sleep, time
from typing import Callable, Dict, Optional, Any, List
from dataclasses import dataclass
from socket import timeout, error

from threading import Thread, Event, Lock
from queue import Empty, Queue

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
    enable_ordering: bool = False
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

        # Receive process and queue
        self._receive_queue: Optional[Queue] = None
        self._receive_thread: Optional[Thread] = None
        self._stop_event: Optional[Event] = None
        self._shared_chunk_buffer: Dict = {}

        # Chunk reassembly buffer
        self._chunk_buffer: Dict[str, list] = {}
        self._buffer_lock = Lock()

        # Transport layer callbacks
        self._send_callback: Optional[Callable[[bytes], None]] = None
        self._receive_callback: Optional[Callable[[int], bytes]] = None

        self._missed_data = 0

        self.logger = Logger.get_instance()

    def start(self):
        """Start listening thread for incoming messages."""
        if not self._receive_callback:
            raise RuntimeError("Transport layer not configured. Call set_transport() first.")

        self._receive_queue = Queue(maxsize=10000)
        self._stop_event = Event()

        self._receive_thread = Thread(
            target=self._receive_loop,
            args=(self._receive_callback, self._receive_queue, self._stop_event,
                  self.config, self._shared_chunk_buffer),
            daemon=True
        )
        self._receive_thread.start()

    @staticmethod
    def _receive_loop(receive_callback, message_queue, stop_event,
                      config, chunk_buffer):
        """Loop di ricezione eseguito in un thread separato."""
        while not stop_event.is_set():
            try:
                _receive_buffer = bytearray()

                # Ricevi lunghezza del messaggio
                data = receive_callback(4)
                if not data:
                    continue

                _receive_buffer.extend(data)
                msg_length = ProtocolMessage.read_lenght_prefix(data)
                total_length = 4 + msg_length

                # Ricevi il resto del messaggio
                while len(_receive_buffer) < total_length:
                    remaining = total_length - len(_receive_buffer)
                    chunk = receive_callback(min(remaining, config.max_chunk_size))
                    if not chunk:
                        break
                    _receive_buffer.extend(chunk)

                if len(_receive_buffer) == total_length:
                    message = ProtocolMessage.from_bytes(_receive_buffer)
                    message.timestamp = time()

                    if message.is_chunk:
                        reconstructed = MessageExchange._handle_chunk_static(message, chunk_buffer)
                        if reconstructed:
                            try:
                                message_queue.put(reconstructed, timeout=0.1)
                            except:
                                pass
                    else:
                        try:
                            message_queue.put(message, timeout=0.1)
                        except:
                            pass

            except (timeout, error):
                continue
            except Exception:
                continue

    @staticmethod
    def _handle_chunk_static(chunk: ProtocolMessage, chunk_buffer: Dict) -> Optional[ProtocolMessage]:
        """Gestisce chunk in modo thread-safe per multiprocessing."""
        message_id = chunk.message_id

        if message_id not in chunk_buffer:
            chunk_buffer[message_id] = [None] * chunk.total_chunks

        chunks_list = chunk_buffer[message_id]
        chunks_list[chunk.chunk_index] = chunk.to_bytes()
        chunk_buffer[message_id] = chunks_list

        # Verifica se tutti i chunk sono arrivati
        if all(c is not None for c in chunks_list):
            chunks = [ProtocolMessage.from_bytes(c) for c in chunks_list]
            del chunk_buffer[message_id]
            return MessageBuilder().reconstruct_from_chunks(chunks)

        return None

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

        if stream_type == StreamType.MOUSE:
            self.send_mouse_data(source=source, target=target, **kwargs)
            return
        elif stream_type == StreamType.KEYBOARD:
            self.send_keyboard_data(source=source, target=target, **kwargs)
            return
        elif stream_type == StreamType.CLIPBOARD:
            self.send_clipboard_data(source=source, target=target, **kwargs)
            return
        elif stream_type == StreamType.FILE:
            self.send_file_data(source=source, target=target, **kwargs)
            return
        elif stream_type == StreamType.COMMAND:
            self.send_command_message(source=source, target=target, **kwargs)
            return
        else:
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
        _receive_buffer = bytearray()

        try:
            data = self._receive_callback(4)
            stime = -time()
            _receive_buffer.extend(data)

            msg_lenght = ProtocolMessage.read_lenght_prefix(data)
            total_length = 4 + msg_lenght
            while len(_receive_buffer) < total_length:
                remaining = total_length - len(_receive_buffer)
                chunk = self._receive_callback(min(remaining, self.config.max_chunk_size))
                if not chunk:
                    return None
                _receive_buffer.extend(chunk)

            if data:
                stime += time()
                print(f"Complete message delay: {stime:.4f}s")
                return self._receive_data(_receive_buffer, instant=instant)
            return None
        except ValueError as e:
            self._missed_data += 1
            return None # Silent
        except (timeout, error) as e:
            return None
        except Exception as e:
            self.logger.log(f"Error receiving message: {e}", Logger.ERROR)
            return None

    def get_received_message(self, timeout: float = 0) -> Optional[ProtocolMessage]:
        """
        Preleva un messaggio dalla coda di ricezione se disponibile.
        I chunk sono già gestiti nel processo di ricezione.

        Args:
            timeout: Tempo di attesa in secondi (0 = non bloccante)

        Returns:
            Messaggio ricevuto o None se la coda è vuota
        """
        if not self._receive_queue:
            return None

        try:
            mex = self._receive_queue.get(
                timeout=timeout if timeout > 0 else None
            )
            return mex
        except Empty:
            return None

    def _receive_data(self, data: bytes, instant: bool = False):
        """
        Receive raw bytes from transport layer and process them.

        Args:
            data: Raw bytes received from network
        """
        try:
            message = ProtocolMessage.from_bytes(data)
            # Debug TODO: To remove
            message.timestamp = time()
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
        #Thread(target=self._dispatch_thread, args=(message,)).start()
        self.dispatch_thread(message)

    def dispatch_thread(self, message: ProtocolMessage):
        """Dispatch message to registered handler."""
        handler = self._handlers.get(message.message_type)
        curtime=time()
        print(f"Pre-dispatch delay: {curtime - message.timestamp:.4f}s")
        if handler:
            try:
                handler(message)
            except Exception as e:
                self.logger.log(f"Error in message handler for {message.message_type}: {e}", Logger.ERROR)
        else:
            self.logger.log(f"No handler registered for message type: {message.message_type}", Logger.ERROR)

    def stop(self, listener: bool = False, timeout: float = 0):
        """Cleanup and shutdown the message exchange layer."""
        if self._stop_event:
            self._stop_event.set()
        if self._receive_thread:
            self._receive_thread.join(timeout=timeout)

        if not listener:
            if self.processor:
                self.processor.stop()

            self._chunk_buffer.clear()


# Esempio di utilizzo
if __name__ == "__main__":
    import socket
    from threading import Thread

    Logger(stdout=print, logging=True)

    # Configurazione
    config = MessageExchangeConfig(
        max_delay_tolerance=0.1,
        max_chunk_size=1024,
        enable_ordering=False,
        parallel_processors=2,
    )

    # Setup socket server e client
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind(('127.0.0.1', 0))
    server_socket.listen(1)
    server_port = server_socket.getsockname()[1]

    # Client socket
    client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client_socket.connect(('127.0.0.1', server_port))

    # Accept connection
    conn, addr = server_socket.accept()
    print(f"Connection established: {addr}")

    # Inizializza exchange per server (riceve)
    server_exchange = MessageExchange(config)

    # Transport callbacks per server
    def server_send(data: bytes):
        conn.sendall(data)

    def server_receive(size: int) -> bytes:
        return conn.recv(size)

    server_exchange.set_transport(server_send, server_receive)

    # Inizializza exchange per client (invia)
    client_exchange = MessageExchange(config)

    # Transport callbacks per client
    def client_send(data: bytes):
        client_socket.sendall(data)

    def client_receive(size: int) -> bytes:
        return client_socket.recv(size)

    client_exchange.set_transport(client_send, client_receive)

    # Registra handlers sul server
    def handle_mouse(msg: ProtocolMessage):
        print(f"Mouse event: {msg.payload}")

    def handle_keyboard(msg: ProtocolMessage):
        print(f"Keyboard event: {msg.payload}")

    def handle_clipboard(msg: ProtocolMessage):
        content_len = len(msg.payload.get('content', ''))
        print(f"Clipboard event received, length: {content_len}")

    server_exchange.register_handler("mouse", handle_mouse)
    server_exchange.register_handler("keyboard", handle_keyboard)
    server_exchange.register_handler("clipboard", handle_clipboard)

    # Avvia ricezione parallela sul server
    server_exchange.start()

    # Thread per processare messaggi ricevuti
    def process_messages():
        while True:
            message = server_exchange.get_received_message(timeout=0.1)
            if message:
                server_exchange.dispatch_thread(message)
            if not message:
                sleep(0.01)

    receive_thread = Thread(target=process_messages, daemon=True)
    receive_thread.start()

    # Test invio messaggi dal client
    print("\n=== Test 1: Simple messages ===")
    client_exchange.send_mouse_data(100, 200, "click", is_pressed=True, dx=0, dy=0)
    client_exchange.send_keyboard_data("A", "press")
    sleep(0.5)

    print("\n=== Test 2: Large clipboard (chunked) ===")
    large_content = "A" * 500000
    client_exchange.send_clipboard_data(large_content)
    sleep(2)

    print("\n=== Test 3: Multiple messages rapid fire ===")
    for i in range(2000):
        client_exchange.send_mouse_data(i * 10, i * 20, "move", dx=1, dy=1)
    sleep(1)

    print("\n=== Test 4: Out of order chunked message ===")
    out_of_order_content = "Out of order test " * 300
    client_exchange.send_clipboard_data(out_of_order_content)
    sleep(2)

    print("\n=== Test 5: Numbers sequence ===")
    numbers_content = "".join(str(i) + " " for i in range(1, 1001))
    client_exchange.send_clipboard_data(numbers_content)
    sleep(2)

    # Cleanup
    print("\n=== Cleanup ===")
    server_exchange.stop()
    client_exchange.stop()
    conn.close()
    client_socket.close()
    server_socket.close()
    print("Test completed")