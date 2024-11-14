import socket
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from time import sleep
from queue import Queue, Empty

from utils.netData import *

BATCH_PROCESS_INTERVAL = 0.01
MAX_BATCH_SIZE = 10
MAX_WORKERS = 10  # Numero massimo di worker nel thread pool

class ServerHandler:
    def __init__(self, connection: socket.socket, command_func: Callable, on_disconnect: Callable, logger: Callable):
        self.processor_thread = None
        self.conn = connection
        self.process_command = command_func
        self.on_disconnect = on_disconnect
        self.log = logger
        self._running = False
        self.thread = None
        self.message_queue = Queue()
        self.batch_ready_event = threading.Event()
        self.executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)

    def start(self):
        self._running = True
        self.thread = threading.Thread(target=self._handle_server_commands, daemon=True)
        try:
            self.thread.start()
            self.log("Client in listening mode.")
        except Exception as e:
            self.log(f"Error starting client: {e}", 2)

        # Start thread to process messages in the queue
        self.processor_thread = threading.Thread(target=self._process_message_queue, daemon=True)
        self.processor_thread.start()

    def stop(self):
        self._running = False
        self.batch_ready_event.set()  # Wake up the processor thread
        self.conn.close()
        self.executor.shutdown(wait=True)
        self.on_disconnect()
        self.log("Client disconnected.", 1)

    def _handle_server_commands(self):
        """Handle commands continuously received from the server."""
        buffer = ""
        while self._running:
            try:
                data = self.conn.recv(CHUNK_SIZE).decode()
                if not data:
                    break
                buffer += data

                while END_DELIMITER in buffer or CHUNK_DELIMITER in buffer:
                    if END_DELIMITER in buffer:
                        # Trova il primo delimitatore di fine batch
                        pos = buffer.find(END_DELIMITER)
                        batch = buffer[:pos]
                        buffer = buffer[pos + len(END_DELIMITER):]  # Rimuovi il batch dal buffer

                        # Aggiungi il batch alla coda
                        self.message_queue.put(batch)
                        self.batch_ready_event.set()
                    elif CHUNK_DELIMITER in buffer:
                        # Trova il primo delimitatore di chunk e rimuovilo dal buffer
                        pos = buffer.find(CHUNK_DELIMITER)
                        chunk = buffer[:pos]
                        buffer = buffer[pos + len(CHUNK_DELIMITER):]

                        # I chunk fanno parte del batch, quindi li concateno al batch
                        self.message_queue.put(chunk)
                        self.batch_ready_event.set()

                sleep(0.001)
            except Exception as e:
                self.log(f"Error receiving data: {e}", 2)
                break
        self.stop()

    def _process_message_queue(self):
        """Process messages from the queue in a batched manner."""
        batch_buffer = []

        while self._running:
            try:
                # Attendi fino a quando ci sono nuovi messaggi o il tempo massimo è trascorso
                self.batch_ready_event.wait(BATCH_PROCESS_INTERVAL)

                # Raccogli i messaggi dalla coda fino a raggiungere la dimensione massima del batch
                while not self.message_queue.empty() and len(batch_buffer) < MAX_BATCH_SIZE:
                    batch_buffer.append(self.message_queue.get())

                # Se il batch è pronto per essere elaborato, processalo
                if batch_buffer:
                    self._process_batch(batch_buffer)
                    batch_buffer.clear()

                # Reset l'evento dopo aver processato il batch
                self.batch_ready_event.clear()

            except Exception as e:
                self.log(f"Error processing message queue: {e}", 2)

    def _process_batch(self, batch_buffer):
        for command in batch_buffer:
            self.executor.submit(self.process_command, command)


class ServerCommandProcessor:
    def __init__(self, on_screen_func, mouse_controller, keyboard_controller, clipboard):
        self.on_screen = on_screen_func
        self.mouse_controller = mouse_controller
        self.keyboard_controller = keyboard_controller
        self.clipboard = clipboard
        self.log = print  # TODO: Usare il logger

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
