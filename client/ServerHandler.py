import socket
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from time import sleep, time
from queue import Queue, Empty

from utils.Logging import Logger
from utils.netData import *

BATCH_PROCESS_INTERVAL = 0.0001
MAX_WORKERS = 10


class ServerHandlerFactory:
    @staticmethod
    def create_server_handler(connection, command_func: Callable):
        return ServerHandler(connection, command_func)


class ServerHandler:
    CHUNK_DELIMITER = CHUNK_DELIMITER.encode()
    END_DELIMITER = END_DELIMITER.encode()

    def __init__(self, connection, command_func: Callable):
        self.conn = connection
        self.process_command = command_func
        self.on_disconnect = None
        self.log = Logger.get_instance().log
        self._running = False
        self.main_thread = None
        self.data_queue = Queue()

        self.executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)

    def start(self):
        self._running = True
        self.main_thread = threading.Thread(target=self._handle_server_commands, daemon=True)
        self.buffer_thread = threading.Thread(target=self._buffer_and_process_batches, daemon=True)
        try:
            self.main_thread.start()
            self.buffer_thread.start()
            self.log("Client in listening mode.")
        except Exception as e:
            self.log(f"Error starting client: {e}", 2)

    def stop(self):
        self._running = False
        self.executor.shutdown(wait=True)
        self.log("Client disconnected.", 1)
        self.conn.close()

    def _handle_server_commands(self):
        """Handle commands continuously received from the server."""
        while self._running:
            try:
                data = self.conn.recv(CHUNK_SIZE)

                if not data:
                    break
                if data == b'\x00':  # Heartbeat
                    continue

                self.data_queue.put(data)
            except socket.timeout:
                continue
            except Exception as e:
                self.log(f"Error receiving data: {e}", 2)
                break
        self.stop()

    def _buffer_and_process_batches(self):
        """Buffer data and process batches in a separate thread."""
        buffer = bytearray()
        last_batch_time = 0
        while self._running:
            try:
                data = self.data_queue.get(timeout=1)
                buffer.extend(data)

                # Timer to process the batch (increase buffer size to avoid processing too many small batches)
                if last_batch_time == 0:
                    last_batch_time = time()
                elif time() - last_batch_time > BATCH_PROCESS_INTERVAL:
                    last_batch_time = 0
                else:
                    continue

                while self.END_DELIMITER in buffer:
                    end_pos = buffer.find(self.END_DELIMITER)
                    batch = buffer[:end_pos]
                    buffer = buffer[end_pos + len(self.END_DELIMITER):]

                    # Remove CHUNK_DELIMITER from the batch
                    batch = batch.replace(self.CHUNK_DELIMITER, b'')

                    self._process_batch(batch.decode())

            except Empty:
                continue
            except Exception as e:
                self.log(f"Error processing data: {e}", 2)

    def _process_batch(self, command):
        self.executor.submit(self.process_command, command)


class ServerCommandProcessor:
    def __init__(self, client):
        self.client = client
        self.on_screen = self.client.on_screen
        self.mouse_controller = self.client.mouse_controller
        self.keyboard_controller = self.client.keyboard_controller
        self.clipboard = self.client.clipboard_listener

        self.stop_event = threading.Event()

        self.keyboard_queue = Queue()  # Needed to process commands sequentially, preserving the order
        self.keyboard_thread = threading.Thread(target=self._process_keyboard_queue, daemon=True)
        self.keyboard_thread.start()

        self.clipboard_queue = Queue()  # Needed to process commands sequentially, preserving the order
        self.clipboard_thread = threading.Thread(target=self._process_clipboard_queue, daemon=True)
        self.clipboard_thread.start()

        self.log = Logger.get_instance().log

    def stop(self):
        self.stop_event.set()
        self.keyboard_thread.join()
        self.clipboard_thread.join()
        self.stop_event.clear()

    def process_command(self, command):
        parts = extract_command_parts(command)
        command_type = parts[0]

        if command_type == "mouse":
            self._process_mouse_command(parts)
        elif command_type == "keyboard":
            self._process_keyboard_command(parts)
        elif command_type == "clipboard":
            self._process_clipboard_command(parts)
        elif command_type == "screen":
            self._process_screen_command(parts)

    def _process_mouse_command(self, parts):
        try:
            event = parts[1]
            x, y = float(parts[2]), float(parts[3])
            is_pressed = parts[4] == "true" if len(parts) > 4 else False
            self.mouse_controller.process_mouse_command(x, y, event, is_pressed)
        except Exception as e:
            self.log(f"Error processing mouse command: {e}", 2)

    def _process_keyboard_queue(self):
        while not self.stop_event.is_set():
            try:
                parts = self.keyboard_queue.get(timeout=1)
                self._process_keyboard_command(parts)
                self.keyboard_queue.task_done()
            except Empty:
                continue

    def _process_keyboard_command(self, parts):
        try:
            event = parts[1]
            key = parts[2]
            self.keyboard_controller.process_key_command(key, event)
        except Exception as e:
            self.log(f"Error processing keyboard command: {e}", 2)

    def _process_clipboard_command(self, parts):
        try:
            content = extract_text(parts[1])
            self.clipboard.set_clipboard(content)
        except Exception as e:
            self.log(f"Error processing clipboard command: {e}", 2)

    def _process_clipboard_queue(self):
        while not self.stop_event.is_set():
            try:
                parts = self.clipboard_queue.get(timeout=1)
                self._process_clipboard_command(parts)
                self.clipboard_queue.task_done()
            except Empty:
                continue

    def _process_screen_command(self, parts):
        try:
            is_on_screen = parts[1] == "true"
            self.on_screen(is_on_screen)
        except Exception as e:
            self.log(f"Error processing screen command: {e}", 2)


class BatchProcessor:
    def __init__(self, process_command_func):
        self.process_command = process_command_func

    def process_batch(self, batch):
        commands = batch.split(CHUNK_DELIMITER)
        for command in commands:
            self.process_command(command)
