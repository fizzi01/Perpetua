import os
import socket
import threading
import time
from queue import Queue, Empty, PriorityQueue

from utils.Logging import Logger
from utils.netData import *

MOUSE_BATCH_INTERVAL = 0.02  # 20 ms, intervallo per inviare il batch dei messaggi del mouse
MOUSE_MAX_BATCH_SIZE = 10  # Massimo numero di messaggi nel batch prima di inviare

KEYBOARD_BATCH_INTERVAL = 0.01  # 20 ms, intervallo per inviare il batch dei messaggi della tastiera
KEYBOARD_MAX_BATCH_SIZE = 7  # Massimo numero di messaggi nel batch prima di inviare

FILE_MAX_BATCH_SIZE = CHUNK_SIZE - END_DELIMITER.encode().__len__()  # Massimo numero di byte nel batch prima di inviare


class QueueManager:
    _instance = None
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

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            with cls._lock:
                if not cls._instance:
                    cls._instance = super(QueueManager, cls).__new__(cls)
        return cls._instance

    def __init__(self, MessageSender, mouse=True, keyboard=True, clipboard=True, file=True):
        if not hasattr(self, 'initialized'):
            self.mouse_queue = Queue()
            self.keyboard_queue = Queue()
            self.clipboard_queue = Queue()
            self.file_queue = Queue()
            self.forward_file_queue = Queue()

            self.MessageSender = MessageSender
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

    def send(self, queue_type, message):
        if queue_type == self.MOUSE:
            self.mouse_queue.put(message)
        elif queue_type == self.KEYBOARD:
            self.keyboard_queue.put(message)
        elif queue_type == self.CLIPBOARD:
            self.clipboard_queue.put(message)

    def send_mouse(self, screen, message):
        if self.manage_mouse:
            self.mouse_queue.put((screen, message))

    def send_keyboard(self, screen, message):
        if self.manage_keyboard:
            self.keyboard_queue.put((screen, message))

    def send_clipboard(self, screen, message):
        if self.manage_clipboard:
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

        # Prepara i messaggi per l'invio come batch
        batch_message = END_DELIMITER.join([format_data(data) for _, data in self.mouse_batch_buffer])
        screen = self.mouse_batch_buffer[0][0]  # Assume che tutti i messaggi del batch siano per lo stesso schermo
        self.MessageSender.send(self.MOUSE_PRIORITY, (screen, batch_message))

        # Pulisce il buffer dopo l'invio
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
                file_path, screen = self.file_queue.get()
                self._send_file(file_path, screen)
            except Empty:
                continue

    def _send_file(self, file_path, screen=None):
        try:
            with open(file_path, 'rb') as file:
                self.log(f"Inizio invio file: {file_path}")
                # Invia file_start con i metadati del file
                file_name = os.path.basename(file_path)
                file_size = os.path.getsize(file_path)
                start_message = format_command(f"file_start {file_name} {file_size} {file_path}")
                self.MessageSender.send(self.FILE_PRIORITY, (screen, start_message))

                # Invia il file in chunk
                while True:
                    chunk = file.read(FILE_MAX_BATCH_SIZE)
                    if not chunk:
                        break
                    encoded_chunk = chunk.decode('latin1')  # Usa 'latin1' per inviare byte come stringa
                    chunck_message = format_command(f"file_chunk {encoded_chunk}")
                    self.MessageSender.send(self.FILE_PRIORITY, (screen, chunck_message))

                # Invia file_end
                end_message = format_command(f"file_end {file_name}")
                self.MessageSender.send(self.FILE_PRIORITY, (screen, end_message))
                self.log(f"File inviato con successo: {file_path}")
        except Exception as e:
            self.log(f"Errore durante l'invio del file {file_path}: {e}", Logger.ERROR)

    def _send_file_batch(self):
        if not self.file_batch_buffer:
            return

        batch_message = END_DELIMITER.join([chunk.decode('latin1') for chunk in self.file_batch_buffer])
        self.MessageSender.send(self.FILE_PRIORITY, batch_message)
        self.file_batch_buffer.clear()

    def _send_keyboard_batch(self):
        if not self.keyboard_batch_buffer:
            return

        # Prepara i messaggi per l'invio come batch
        batch_message = CHUNK_DELIMITER.join([format_data(data) for _, data in self.keyboard_batch_buffer])
        screen = self.keyboard_batch_buffer[0][0]  # Assume che tutti i messaggi del batch siano per lo stesso schermo
        self.MessageSender.send(self.KEYBOARD_PRIORITY, (screen, batch_message))

        # Pulisce il buffer dopo l'invio
        self.keyboard_batch_buffer.clear()

    def _process_clipboard_queue(self):
        while not self._stop_event.is_set():
            try:
                message = self.clipboard_queue.get(timeout=0.1)
                self.MessageSender.send(self.CLIPBOARD_PRIORITY, message)
            except Empty:
                continue

    def join(self):
        self._stop_event.set()
        for thread in self._thread_pool:
            thread.join()
        self._threads_started = False
        self._stop_event.clear()

    def is_alive(self):
        return self._threads_started


class BaseMessageQueueManager:
    _instance = None
    _lock = threading.Lock()
    _stop_event = threading.Event()

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            with cls._lock:
                if not cls._instance:
                    cls._instance = super(BaseMessageQueueManager, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        if not hasattr(self, 'initialized'):
            self.send_queue = PriorityQueue()

            self._sending_thread = threading.Thread(target=self._process_send_queue, daemon=True)
            self._threads_started = False
            self.initialized = True

            self.log = Logger.get_instance().log

    def join(self):
        self._stop_event.set()
        if self._threads_started:
            self._sending_thread.join()
            self._threads_started = False
        self._stop_event.clear()

    def is_alive(self):
        return self._threads_started

    def start(self):
        if not self._threads_started:
            self._sending_thread.start()
            self._threads_started = True

    def send(self, priority, message):
        self.send_queue.put((priority, message))

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

    @staticmethod
    def _send_in_chunks(conn, data):
        # Send data in chunks to ensure it fits within the buffer limit
        data_bytes = data.encode()
        data_length = len(data_bytes)

        chunk_size_with_delimiter = CHUNK_SIZE - len(CHUNK_DELIMITER.encode())
        chunk_size_with_end_delimiter = CHUNK_SIZE - len(END_DELIMITER.encode())
        real_chunk_size = min(chunk_size_with_delimiter, chunk_size_with_end_delimiter)

        if data_length > real_chunk_size:
            chunks = [data_bytes[i:i + real_chunk_size] for i in range(0, data_length, real_chunk_size)]
            for i, chunk in enumerate(chunks):
                if i == len(chunks) - 1:
                    # Last chunk - add the END_DELIMITER
                    conn.send(chunk + END_DELIMITER.encode())
                else:
                    # Intermediate chunk - add the CHUNK_DELIMITER
                    conn.send(chunk + CHUNK_DELIMITER.encode())
        else:
            # Data fits in a single chunk - add END_DELIMITER
            conn.send(data_bytes + END_DELIMITER.encode())


class ServerMessageQueueManager(BaseMessageQueueManager):

    def __init__(self, connected_clients, get_client, change_screen):
        super().__init__()
        self.get_connected_clients = connected_clients
        self.get_client = get_client
        self.change_screen = change_screen

    def _send_message(self, message):
        screen, data = message

        if screen == "all":
            # Send the data to all connected clients
            for key in self.get_connected_clients():
                self._send_to_client(key, data)
        else:
            # Send data to the specified client
            self._send_to_client(screen, data)

    def _send_to_client(self, client_key, data):
        try:
            if not client_key or client_key == "None":  # Skip sending data if the client key is None
                return

            # Prepare data to send
            formatted_data = format_data(data)

            conn = self.get_client(client_key)
            if not conn:
                return  # Skip sending data if the client is not connected

            if not conn.is_socket_open():
                return  # Skip sending data if the socket is closed

            # Send the data in chunks if needed
            self._send_in_chunks(conn, formatted_data)

        except KeyError as e:
            self.log(f"Error sending data to client {client_key}: {e}", Logger.ERROR)
        except socket.error as e:
            self.log(f"Can't communicate with client {client_key}: {e}", Logger.ERROR)
            self.change_screen()
        except Exception as e:
            self.log(f"Error sending data to client {client_key}: {e}", Logger.ERROR)


class ClientMessageQueueManager(BaseMessageQueueManager):

    def __init__(self, conn):
        if not hasattr(self, 'initialized'):
            super().__init__()

        self.conn = conn

    def _send_message(self, message):
        try:
            if not self.conn.is_socket_open():
                return
            if isinstance(message, tuple):
                screen, data = message
                formatted_data = format_data(data)
                self._send_in_chunks(self.conn, formatted_data)
            else:
                self._send_in_chunks(self.conn, message)
        except Exception as e:
            self.log(f"Error sending data: {e}", Logger.ERROR)
