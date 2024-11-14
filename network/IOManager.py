import socket
import threading
from queue import Queue, Empty

from utils.Logging import Logger
from utils.netData import *

from server.Server import Server


class QueueManager:
    _instance = None
    _lock = threading.Lock()
    _stop_event = threading.Event()

    MOUSE = "mouse"
    KEYBOARD = "keyboard"
    CLIPBOARD = "clipboard"

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            with cls._lock:
                if not cls._instance:
                    cls._instance = super(QueueManager, cls).__new__(cls)
        return cls._instance

    def __init__(self, MessageSender=None):
        if not hasattr(self, 'initialized'):
            self.mouse_queue = Queue()
            self.keyboard_queue = Queue()
            self.clipboard_queue = Queue()
            self.MessageSender = MessageSender
            self._thread_pool = []
            self._threads_started = False
            self.initialized = True

    def send(self, queue_type, message):
        if queue_type == self.MOUSE:
            self.mouse_queue.put(message)
        elif queue_type == self.KEYBOARD:
            self.keyboard_queue.put(message)
        elif queue_type == self.CLIPBOARD:
            self.clipboard_queue.put(message)

    def start(self):
        if not self._threads_started:
            self._thread_pool.append(threading.Thread(target=self._process_mouse_queue, daemon=True))
            self._thread_pool.append(threading.Thread(target=self._process_keyboard_queue, daemon=True))
            self._thread_pool.append(threading.Thread(target=self._process_clipboard_queue, daemon=True))
            for thread in self._thread_pool:
                thread.start()
            self._threads_started = True

    def _process_mouse_queue(self):
        while not self._stop_event.is_set():
            try:
                message = self.mouse_queue.get(timeout=0.01)
                self.MessageSender.send(message)
            except Empty:
                continue

    def _process_keyboard_queue(self):
        while not self._stop_event.is_set():
            try:
                message = self.keyboard_queue.get(timeout=0.1)
                self.MessageSender.send(message)
            except Empty:
                continue

    def _process_clipboard_queue(self):
        while not self._stop_event.is_set():
            try:
                message = self.clipboard_queue.get(timeout=0.1)
                self.MessageSender.send(message)
            except Empty:
                continue

    def stop(self):
        self._stop_event.set()
        for thread in self._thread_pool:
            thread.join()
        self._threads_started = False
        self._stop_event.clear()


class MessageQueueManager:
    _instance = None
    _lock = threading.Lock()
    _stop_event = threading.Event()
    log = Logger.get_instance().log

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            with cls._lock:
                if not cls._instance:
                    cls._instance = super(MessageQueueManager, cls).__new__(cls)
        return cls._instance

    def __init__(self, server: Server):
        if not hasattr(self, 'initialized'):
            self.send_queue = Queue()
            self.server = server
            self._sending_thread = threading.Thread(target=self._process_send_queue, daemon=True)
            self._threads_started = False
            self.initialized = True

    def stop(self):
        self._stop_event.set()
        self._sending_thread.join()
        self._threads_started = False
        self._stop_event.clear()

    def start(self):
        if not self._threads_started:
            self._sending_thread.start()
            self._threads_started = True

    def send(self, message):
        self.send_queue.put(message)

    def _process_send_queue(self):
        while not self._stop_event.is_set():
            try:
                message = self.send_queue.get(timeout=0.1)
                self._send(message)
            except Empty:
                continue

    def _send(self, message):
        try:
            screen, data = message.get(timeout=0.1)

            if screen == "all":
                for key in self.server.get_connected_clients():
                    if key:
                        self._send(message)
            else:
                # Preparing data to send
                data = format_data(data)

                try:
                    conn = self.server.get_client(screen)
                    if not conn:
                        raise KeyError

                    # Split the command into chunks if it's too long
                    if len(data) > CHUNK_SIZE - len(END_DELIMITER):
                        chunks = [data[i:i + CHUNK_SIZE] for i in range(0, len(data), CHUNK_SIZE)]
                        for i, chunk in enumerate(chunks):
                            if i == len(chunks) - 1 and len(chunk) > CHUNK_SIZE - len(END_DELIMITER):
                                conn.send(chunk.encode())
                                conn.send(END_DELIMITER.encode())
                            elif i == len(chunks) - 1:
                                chunk = chunk.encode() + END_DELIMITER.encode()
                                conn.send(chunk)
                            else:
                                chunk = chunk.encode() + CHUNK_DELIMITER.encode()
                                conn.send(chunk)
                    else:
                        conn.send(data.encode() + END_DELIMITER.encode())
                except socket.error as e:
                    self.log(f"Can't communicate with client {screen}: {e}", Logger.ERROR)
                    self.server.change_screen()
                except KeyError:
                    self.log(f"Error sending data to client {screen}: Client not found.", Logger.ERROR)
                except Exception as e:
                    self.log(f"Error sending data to client {screen}: {e}", Logger.ERROR)
        except Empty:
            return
        except Exception as e:
            self.log(f"Error in message processing: {e}", Logger.ERROR)
            return
