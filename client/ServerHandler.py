import socket
import threading
from collections.abc import Callable
from time import sleep, time
from queue import Queue, Empty

from client.ClientState import ClientState, ControlledState
from inputUtils.FileTransferEventHandler import FileTransferEventHandler
from utils.Logging import Logger
from utils.netData import *

BATCH_PROCESS_INTERVAL = 0.0001
TIMEOUT = 0.00001
MAX_WORKERS = 30


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
        self.buffer_thread = None
        self.init_threads()

        self.data_queue = Queue()

    def init_threads(self):
        self.main_thread = threading.Thread(target=self._handle_server_commands, daemon=True)
        self.buffer_thread = threading.Thread(target=self._buffer_and_process_batches, daemon=True)

    def start_threads(self):
        self.main_thread.start()
        self.buffer_thread.start()

    def stop_threads(self):
        self._running = False
        # check if main_thread is current thread
        if threading.current_thread() != self.main_thread:
            self.main_thread.join()
        self.buffer_thread.join()

    def start(self):
        self._running = True
        try:
            self.start_threads()
            self.log("Client in listening mode.")
        except Exception as e:
            self.log(f"Error starting client: {e}", 2)

    def stop(self):
        self.stop_threads()
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
                sleep(TIMEOUT)
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
                while not self.data_queue.empty() or time() - last_batch_time < BATCH_PROCESS_INTERVAL:
                    buffer.extend(self.data_queue.get())

                while self.END_DELIMITER in buffer:
                    end_pos = buffer.find(self.END_DELIMITER)
                    batch = buffer[:end_pos]
                    buffer = buffer[end_pos + len(self.END_DELIMITER):]

                    # Remove CHUNK_DELIMITER from the batch
                    batch = batch.replace(self.CHUNK_DELIMITER, b'')

                    self._process_batch(batch.decode())
                sleep(TIMEOUT)
            except Empty:
                sleep(TIMEOUT)
                continue
            except Exception as e:
                self.log(f"Error processing data: {e}", 2)

    def _process_batch(self, command):
        self.process_command(command)


# TODO: Apply command pattern to the command processing (#CodeCleanup)
class ServerCommandProcessor:
    def __init__(self, client):
        self.client = client
        self.client_state = ClientState()
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

        self.mouse_queue = Queue()  # Needed to process commands sequentially, preserving the order
        self.mouse_thread = threading.Thread(target=self._process_mouse_queue, daemon=True)
        self.mouse_thread.start()

        self.file_queue = Queue()
        self.file_thread = threading.Thread(target=self._process_file_queue, daemon=True)
        self.file_thread.start()

        self.fileTransferHandler = FileTransferEventHandler()

        self.log = Logger.get_instance().log

    def stop(self):
        self.stop_event.set()
        self.keyboard_thread.join()
        self.clipboard_thread.join()
        self.mouse_thread.join()
        self.file_thread.join()
        self.stop_event.clear()

    def process_command(self, command):
        parts = extract_command_parts(command)
        command_type = parts[0]

        if command_type == "file_request":
            self.file_queue.put(parts)
        elif command_type == "file_start":
            self.file_queue.put(parts)
        elif command_type == "file_chunk":
            self.file_queue.put(parts)
        elif command_type == "file_end":
            self.file_queue.put(parts)
        elif command_type == "mouse":
            self.mouse_queue.put(parts)
        elif command_type == "keyboard":
            self._process_keyboard_command(parts)
        elif command_type == "clipboard":
            self._process_clipboard_command(parts)
        elif command_type == "screen":
            self._process_screen_command(parts)

    def _process_file_queue(self):
        while not self.stop_event.is_set():
            try:
                parts = self.file_queue.get(timeout=1)
                self._process_file_command(parts)
                self.file_queue.task_done()
            except Empty:
                continue

    def _process_file_command(self, parts):
        try:
            command_type = parts[0]
            if command_type == "file_request":
                self.fileTransferHandler.handle_file_request(None)
            elif command_type == "file_start":
                file_info = {
                    "file_name": parts[1],
                    "file_size": int(parts[2]),
                }
                self.fileTransferHandler.handle_file_start(file_info)
            elif command_type == "file_chunk":
                self.fileTransferHandler.handle_file_chunk(parts[1:])
            elif command_type == "file_end":
                self.fileTransferHandler.handle_file_end()
        except Exception as e:
            self.log(f"Error processing file command: {e}", 2)

    def _process_mouse_queue(self):
        while not self.stop_event.is_set():
            try:
                parts = self.mouse_queue.get(timeout=1)
                # If client_state is Hiddle, set it to Controlled
                if not self.client_state.is_controlled():
                    self.client_state.set_state(ControlledState())

                self._process_mouse_command(parts)
                self.mouse_queue.task_done()
            except Empty:
                continue

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
