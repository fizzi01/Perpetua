import base64
import os
import socket
import ssl
import threading
import time
import urllib.parse
import zlib
from queue import Queue, Empty
import heapq

from utils.Interfaces import IServerContext, IMessageService, IMessageQueueManager, IClientContext
from utils.Logging import Logger
from utils.net.netData import *
from utils.net.ChunkManager import ChunkManager
from utils.protocol.message import MessageBuilder, ProtocolMessage
from utils.protocol.adapter import ProtocolAdapter

from utils.Interfaces import IBaseCommand

# Optimized batch settings for the new protocol
MOUSE_BATCH_INTERVAL = 0.015  # 15ms - faster for better responsiveness
MOUSE_MAX_BATCH_SIZE = 5      # Smaller batches for structured messages

KEYBOARD_BATCH_INTERVAL = 0.01  # 10ms - faster keyboard response
KEYBOARD_MAX_BATCH_SIZE = 5      # Smaller batches

# Use ChunkManager for file size calculations
FILE_MAX_BATCH_SIZE = ChunkManager.get_max_message_size()


class StablePriorityQueue:
    def __init__(self):
        self._queue = []  # La heap
        self._counter = 0  # Contatore crescente per preservare l'ordine
        self._condition = threading.Condition()  # Sincronizzazione per il timeout

    def put(self, priority, item):
        with self._condition:
            # Inserisce nella heap una tupla con: (priorità, contatore, elemento)
            heapq.heappush(self._queue, (priority, self._counter, item))
            self._counter += 1
            # Notifica i thread in attesa che un elemento è stato aggiunto
            self._condition.notify()

    def get(self, timeout=None):
        with self._condition:
            # Attendi finché non ci sono elementi disponibili o il timeout scade
            end_time = time.time() + timeout if timeout is not None else None
            while not self._queue:
                remaining = end_time - time.time() if end_time is not None else None
                if remaining is not None and remaining <= 0:
                    raise Empty
                self._condition.wait(remaining)

            # Estrai l'elemento con priorità più alta (o ordine corretto a parità di priorità)
            return None, heapq.heappop(self._queue)[2]

    def peek(self):
        # Visualizza il prossimo elemento senza rimuoverlo
        with self._condition:
            if not self._queue:
                raise IndexError("peek from an empty priority queue")
            return self._queue[0][2]

    def is_empty(self):
        with self._condition:
            return len(self._queue) == 0

    def size(self):
        with self._condition:
            return len(self._queue)


class MessageService(IMessageService):
    _lock = threading.Lock()
    _stop_event = threading.Event()

    MOUSE = "mouse"
    KEYBOARD = "keyboard"
    CLIPBOARD = "clipboard"

    SCREEN_NOTIFICATION_PRIORITY = 1
    MOUSE_PRIORITY = 4
    KEYBOARD_PRIORITY = 3
    CLIPBOARD_PRIORITY = 2
    FILE_PRIORITY = 5

    def __init__(self, message_sender: IMessageQueueManager, mouse=True, keyboard=True, clipboard=True, file=True):
        super().__init__()

        self.mouse_queue = Queue()
        self.keyboard_queue = Queue()
        self.clipboard_queue = Queue()
        self.file_queue = Queue()
        self.forward_file_queue = Queue()

        self.MessageSender = message_sender
        self._thread_pool = []
        self._threads_started = False
        self.initialized = True

        # Buffer for events
        self.mouse_batch_buffer = []
        self.keyboard_batch_buffer = []
        self.file_batch_buffer = []

        # Flags to manage specific queues
        self.manage_mouse = mouse
        self.manage_keyboard = keyboard
        self.manage_clipboard = clipboard
        self.manage_files = file

        self.log = Logger.get_instance().log
        
        # Protocol support
        self.message_builder = MessageBuilder()
        self.protocol_adapter = ProtocolAdapter()
        self.use_structured_protocol = True  # Enable new protocol by default

    def send(self, queue_type, message):
        if queue_type == self.MOUSE:
            self.mouse_queue.put(message)
        elif queue_type == self.KEYBOARD:
            self.keyboard_queue.put(message)
        elif queue_type == self.CLIPBOARD:
            self.clipboard_queue.put(message)

    def send_mouse(self, screen, message):
        if self.manage_mouse:

            if isinstance(message, IBaseCommand):
                # Convert BaseCommand directly to ProtocolMessage
                try:
                    structured_msg = message.to_protocol_message(source="input", target=screen)
                    self.mouse_queue.put((screen, structured_msg))
                except Exception as e:
                    # Fallback to legacy string if conversion fails
                    self.log(f"Failed to convert BaseCommand to ProtocolMessage: {e}", Logger.ERROR)
                    self.mouse_queue.put((screen, message.to_legacy_string()))
            elif isinstance(message, str):
                # Parse legacy mouse command and create structured message
                parts = message.split()
                if len(parts) >= 4:
                    try:
                        event = parts[0]
                        x = float(parts[1])
                        y = float(parts[2])
                        is_pressed = parts[3] == "true" if len(parts) > 3 else False
                        
                        structured_msg = self.message_builder.create_mouse_message(
                            x=x, y=y, event=event, is_pressed=is_pressed, target=screen
                        )
                        self.mouse_queue.put((screen, structured_msg))
                    except (ValueError, IndexError):
                        # Fallback to legacy format for malformed commands
                        self.mouse_queue.put((screen, message))
                else:
                    self.mouse_queue.put((screen, message))
            elif isinstance(message, ProtocolMessage):
                # Already structured message
                self.mouse_queue.put((screen, message))
            else:
                # Convert other types to structured messages
                try:
                    x = getattr(message, 'x', 0)
                    y = getattr(message, 'y', 0)
                    event = getattr(message, 'event', 'move')
                    is_pressed = getattr(message, 'is_pressed', False)
                    
                    structured_msg = self.message_builder.create_mouse_message(
                        x=x, y=y, event=event, is_pressed=is_pressed, target=screen
                    )
                    self.mouse_queue.put((screen, structured_msg))
                except:
                    # Last resort fallback
                    self.mouse_queue.put((screen, str(message)))

    def send_keyboard(self, screen, message):
        if self.manage_keyboard:
            
            
            if isinstance(message, IBaseCommand):
                # Convert BaseCommand directly to ProtocolMessage  
                try:
                    structured_msg = message.to_protocol_message(source="input", target=screen)
                    self.keyboard_queue.put((screen, structured_msg))
                except Exception as e:
                    # Fallback to legacy string if conversion fails
                    self.log(f"Failed to convert BaseCommand to ProtocolMessage: {e}", Logger.ERROR)
                    self.keyboard_queue.put((screen, message.to_legacy_string()))
            elif isinstance(message, ProtocolMessage):
                # Already structured message
                self.keyboard_queue.put((screen, message))
            else:
                # Handle legacy string messages and other types
                self.keyboard_queue.put((screen, message))

    def send_clipboard(self, screen, message):
        if self.manage_clipboard:
            
            
            if isinstance(message, IBaseCommand):
                # Convert BaseCommand directly to ProtocolMessage
                try:
                    structured_msg = message.to_protocol_message(source="input", target=screen)
                    self.clipboard_queue.put((screen, structured_msg))
                except Exception as e:
                    # Fallback to legacy string if conversion fails
                    self.log(f"Failed to convert BaseCommand to ProtocolMessage: {e}", Logger.ERROR)
                    self.clipboard_queue.put((screen, message.to_legacy_string()))
            elif isinstance(message, ProtocolMessage):
                # Already structured message
                self.clipboard_queue.put((screen, message))
            else:
                # Handle legacy string messages and other types
                self.clipboard_queue.put((screen, message))

    def send_screen_notification(self, screen, message):
        self.MessageSender.send(self.SCREEN_NOTIFICATION_PRIORITY, (screen, message))

    def send_file_request(self, screen, message):
        # Invia una richiesta per il file al server
        self.MessageSender.send(self.FILE_PRIORITY, (screen, message))

    def send_file_copy(self, screen, message):
        # Notify the server that the file has been copied
        self.MessageSender.send(self.FILE_PRIORITY, (screen, message))

    def send_file(self, file_path, screen=None):
        if self.manage_files:
            self.file_queue.put((file_path, screen))

    def forward_file_data(self, screen, data):
        self.MessageSender.send(self.FILE_PRIORITY, (screen, data))

    def start(self):
        if not self.MessageSender.is_alive():
            self.MessageSender.start()

        if not self._threads_started:
            if self.manage_mouse:
                self._thread_pool.append(threading.Thread(target=self._process_mouse_queue, daemon=True))
            if self.manage_keyboard:
                self._thread_pool.append(threading.Thread(target=self._process_keyboard_queue, daemon=True))
            if self.manage_clipboard:
                self._thread_pool.append(threading.Thread(target=self._process_clipboard_queue, daemon=True))
            if self.manage_files:
                self._thread_pool.append(threading.Thread(target=self._process_file_queue, daemon=True))
            for thread in self._thread_pool:
                thread.start()
            self._threads_started = True

    def _process_mouse_queue(self):
        last_send_time = time.time()

        while not self._stop_event.is_set():
            try:
                message = self.mouse_queue.get(timeout=MOUSE_BATCH_INTERVAL)
                self.mouse_batch_buffer.append(message)

                # Control if it's time to send the batch
                current_time = time.time()
                if (len(self.mouse_batch_buffer) >= MOUSE_MAX_BATCH_SIZE or
                        current_time - last_send_time >= MOUSE_BATCH_INTERVAL):
                    # Invia i messaggi accumulati come batch
                    self._send_mouse_batch()
                    last_send_time = current_time

            except Empty:
                # Se la coda è vuota, controlla se è il momento di inviare i messaggi rimanenti
                if self.mouse_batch_buffer:
                    current_time = time.time()
                    if current_time - last_send_time >= MOUSE_BATCH_INTERVAL:
                        self._send_mouse_batch()
                        last_send_time = current_time
                continue

    def _send_mouse_batch(self):
        if not self.mouse_batch_buffer:
            return

        # For structured protocol, send messages individually to preserve timestamps
        # This ensures proper chronological ordering on the receiving end
        for screen, message in self.mouse_batch_buffer:
            self.MessageSender.send(self.MOUSE_PRIORITY, (screen, message))

        # Clear buffer after sending
        self.mouse_batch_buffer.clear()

    def _process_keyboard_queue(self):
        last_send_time = time.time()

        while not self._stop_event.is_set():
            try:
                message = self.keyboard_queue.get(timeout=0.01)
                self.keyboard_batch_buffer.append(message)

                # Controlla se è il momento di inviare il batch
                current_time = time.time()
                if (len(self.keyboard_batch_buffer) >= KEYBOARD_MAX_BATCH_SIZE or
                        current_time - last_send_time >= KEYBOARD_BATCH_INTERVAL):
                    # Invia i messaggi accumulati come batch
                    self._send_keyboard_batch()
                    last_send_time = current_time

            except Empty:
                # Se la coda è vuota, controlla se è il momento di inviare i messaggi rimanenti
                if self.keyboard_batch_buffer:
                    current_time = time.time()
                    if current_time - last_send_time >= KEYBOARD_BATCH_INTERVAL:
                        self._send_keyboard_batch()
                        last_send_time = current_time
                continue

    def _process_file_queue(self):
        while not self._stop_event.is_set():
            try:
                file_path, screen = self.file_queue.get(timeout=0.2)
                self._send_file(file_path, screen)
            except Empty:
                continue

    def _send_file(self, file_path, screen=None):
        try:
            with open(file_path, 'rb') as file:
                # Send file_start with metadata using ProtocolMessage
                file_name = urllib.parse.quote(os.path.basename(file_path))
                file_size = os.path.getsize(file_path)

                start_message = self.message_builder.create_file_message(
                    command="start",
                    data={
                        "filename": file_name,
                        "size": file_size
                    },
                    target=screen
                )
                self.MessageSender.send(self.FILE_PRIORITY, (screen, start_message))
                self.log(f"Starting file sharing: {file_name} ({file_size} bytes)")

                # Use ChunkManager for optimal chunk size calculation
                optimal_chunk_size = int(ChunkManager.get_max_message_size() * 0.6)  # Conservative for encoding overhead
                chunk_index = 0

                while True:
                    chunk = file.read(optimal_chunk_size)
                    if not chunk:
                        break

                    # Compress and encode chunk
                    compressed_chunk = zlib.compress(chunk)
                    encoded_chunk = base64.b64encode(compressed_chunk).decode()
                    
                    chunk_message = self.message_builder.create_file_message(
                        command="chunk",
                        data={
                            "data": encoded_chunk,
                            "index": chunk_index
                        },
                        target=screen
                    )
                    chunk_index += 1

                    self.MessageSender.send(self.FILE_PRIORITY, (screen, chunk_message))

                # Send file_end using ProtocolMessage
                end_message = self.message_builder.create_file_message(
                    command="end",
                    data={"filename": file_name},
                    target=screen
                )
                self.MessageSender.send(self.FILE_PRIORITY, (screen, end_message))
                self.log(f"File shared successfully: {file_path}")
        except Exception as e:
            self.log(f"Error during file sharing {file_path}: {e}", Logger.ERROR)

    def _send_keyboard_batch(self):
        if not self.keyboard_batch_buffer:
            return

        # Group messages by screen for efficient processing
        screen_messages = {}
        for screen, message in self.keyboard_batch_buffer:
            if screen not in screen_messages:
                screen_messages[screen] = []
            screen_messages[screen].append(message)

        # Send batched or individual messages per screen
        for screen, messages in screen_messages.items():
            # Check if messages can be batched (only legacy strings)
            legacy_messages = []
            structured_messages = []
            
            for message in messages:
                
                if isinstance(message, IBaseCommand):
                    structured_messages.append(message)
                elif isinstance(message, str):
                    legacy_messages.append(message)
                else:
                    structured_messages.append(message)
            
            # Send legacy messages as batch if multiple
            if len(legacy_messages) > 1:
                batch_message = "|".join([format_data(msg) for msg in legacy_messages])
                self.MessageSender.send(self.KEYBOARD_PRIORITY, (screen, batch_message))
            elif len(legacy_messages) == 1:
                self.MessageSender.send(self.KEYBOARD_PRIORITY, (screen, legacy_messages[0]))
                
            # Send structured messages individually to preserve structure
            for message in structured_messages:
                self.MessageSender.send(self.KEYBOARD_PRIORITY, (screen, message))

        # Clear buffer after sending
        self.keyboard_batch_buffer.clear()

    def _process_clipboard_queue(self):
        while not self._stop_event.is_set():
            try:
                message = self.clipboard_queue.get(timeout=0.1)
                # Handle tuple format (screen, message) or direct message
                if isinstance(message, tuple):
                    screen, clipboard_data = message
                    self.MessageSender.send(self.CLIPBOARD_PRIORITY, (screen, clipboard_data))
                else:
                    # Direct message format for backward compatibility
                    self.MessageSender.send(self.CLIPBOARD_PRIORITY, message)
            except Empty:
                continue

    def join(self, timeout=None):
        self._stop_event.set()

        if self.MessageSender.is_alive():
            self.MessageSender.join()

        for thread in self._thread_pool:
            thread.join()
        self._threads_started = False
        self._thread_pool.clear()
        self._stop_event.clear()

    def is_alive(self):
        return self._threads_started


class BaseMessageQueueManager(threading.Thread, IMessageQueueManager):
    _stop_event = threading.Event()

    def __init__(self):
        super().__init__()

        self.send_queue = StablePriorityQueue()

        self._sending_thread = threading.Thread(target=self._process_send_queue, daemon=True)
        self._threads_started = False
        self.initialized = True

        self.log = Logger.get_instance().log
        
        # Use new ChunkManager for efficient data transmission
        self.chunk_manager = ChunkManager()

    def join(self, timeout=None):
        self._stop_event.set()
        if self._threads_started:
            self._sending_thread.join()
            self._threads_started = False
        self._stop_event.clear()

    def is_alive(self):
        return self._threads_started

    def start(self):
        if not self._threads_started:
            if not self._sending_thread.is_alive():
                self._sending_thread = threading.Thread(target=self._process_send_queue, daemon=True)
                self._sending_thread.start()
            self._threads_started = True

    def send(self, priority, message):
        self.send_queue.put(priority, message)

    def _process_send_queue(self):
        while not self._stop_event.is_set():
            try:
                _, message = self.send_queue.get(timeout=0.1)
                self._send_message(message)
            except Empty:
                continue
            except Exception as e:
                self.log(f"Error processing send queue: {e}", Logger.ERROR)

    def _send_message(self, message):
        pass

    def _send_to_client(self, client_key, data):
        pass

    def _send_data_efficiently(self, conn, data):
        """
        Send data using the improved ChunkManager for better efficiency.
        
        Args:
            conn: Network connection
            data: Data to send (can be ProtocolMessage or string)
        """
        try:
            self.chunk_manager.send_data(conn, data)
        except ssl.SSLEOFError:
            raise
        except Exception as e:
            raise ConnectionError(f"Failed to send data efficiently: {e}")

    @staticmethod
    def _send_in_chunks(conn, data):
        """
        Legacy chunking method - kept for backward compatibility.
        For new code, use _send_data_efficiently instead.
        """
        # Create a temporary ChunkManager for legacy calls
        chunk_manager = ChunkManager()
        try:
            chunk_manager.send_data(conn, data)
        except ssl.SSLEOFError as e:
            raise e
        except Exception as e:
            raise e


class ServerMessageQueueManager(BaseMessageQueueManager):

    def __init__(self, context: IServerContext):
        super().__init__()
        self.get_connected_clients = context.get_connected_clients
        self.get_client = context.get_client
        self.change_screen = context.change_screen

    def _send_message(self, message):
        screen, data = message

        if screen == "all":
            # Send the data to all connected clients
            for key in self.get_connected_clients():
                self._send_to_client(key, data)
        else:
            # Send data to the specified client
            self._send_to_client(screen, data)

    def _send_to_client(self, client_key, data, max_retries=20, retry_delay=0.5):
        retries = 0
        try:
            if not client_key or client_key is None:  # Skip sending data if the client key is None
                return

            conn = self.get_client(client_key)
            if not conn:
                return  # Skip sending data if the client is not connected

            if not conn.is_socket_open():
                return  # Skip sending data if the socket is closed

            # Use improved chunk manager for efficient transmission
            if isinstance(data, (ProtocolMessage, str)):
                # Send directly using ChunkManager
                self._send_data_efficiently(conn, data)
            else:
                # Format legacy data and send
                formatted_data = format_data(data)
                self._send_data_efficiently(conn, formatted_data)

        except KeyError as e:
            self.log(f"Error sending data to client {client_key}: {e}", Logger.ERROR)
        except ssl.SSLEOFError:
            while retries < max_retries:
                retries += 1
                self.log(f"Retrying to send data to client {client_key} (attempt {retries}/{max_retries})")
                conn = self.get_client(client_key)

                # Check socket status
                if conn and conn.is_socket_open():
                    try:
                        if isinstance(data, (ProtocolMessage, str)):
                            self._send_data_efficiently(conn, data)
                        else:
                            formatted_data = format_data(data)
                            self._send_data_efficiently(conn, formatted_data)
                        return  # Exit the loop if the data is sent successfully
                    except:
                        pass  # Continue retrying

                time.sleep(retry_delay)  # Wait before retry
            self.log(f"Can't communicate with client {client_key}", Logger.ERROR)
            self.change_screen()
        except socket.error as e:
            self.log(f"Can't communicate with client {client_key}: {e}", Logger.ERROR)
            self.change_screen()
        except Exception as e:
            self.log(f"Error sending data to client {client_key}: {e}", Logger.ERROR)


class ClientMessageQueueManager(BaseMessageQueueManager):

    def __init__(self, context: IClientContext):
        super().__init__()
        self.conn = context.get_connected_server

    def _send_message(self, message):
        try:
            if not self.conn().is_socket_open():
                return
            if isinstance(message, tuple):
                screen, data = message
                # Use improved chunk manager for client communication
                self._send_data_efficiently(self.conn(), data)
            else:
                # Direct message sending
                self._send_data_efficiently(self.conn(), message)
        except Exception as e:
            self.log(f"Error sending data: {e}", Logger.ERROR)
