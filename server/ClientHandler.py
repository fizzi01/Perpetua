import socket
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from queue import Queue

from server.Command import CommandFactory
from utils.Logging import Logger
from utils.netData import *

BATCH_PROCESS_INTERVAL = 0.01
MAX_BATCH_SIZE = 10
MAX_WORKERS = 10


# Factory Pattern per la creazione dei ClientHandler
class ClientHandlerFactory:
    @staticmethod
    def create_client_handler(conn, addr, command_processor):
        return ClientHandler(conn, addr, command_processor)


class ClientHandler:
    """
    Classe per la gestione di un client connesso al server.
    :param conn: Connessione del client
    :param address: Indirizzo del client
    :param process: Funzione di processamento del comando ricevuto
    :param on_disconnect: Funzione da chiamare alla disconnessione del client
    """

    def __init__(self, conn, address, command_processor):
        self.conn = conn
        self.address = address
        self.logger = Logger.get_instance().log
        self._running = False
        self.thread = None

        self.process = command_processor

        self.message_queue = Queue()
        self.clipboard_queue = Queue()
        self.batch_ready_event = threading.Event()
        self.executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)

    def start(self):
        self.thread = threading.Thread(target=self._handle, daemon=True)
        self._running = True
        try:
            self.thread.start()
            self.logger(f"Client {self.address} connected.", 1)
        except Exception as e:
            self.logger(f"Failed to start client connection: {e}", 2)

        # Start thread to process messages in the queue
        self.processor_thread = threading.Thread(target=self._process_message_queue, daemon=True)
        self.processor_thread.start()

        self.clipboard_thread = threading.Thread(target=self._process_clipboard_queue, daemon=True)
        self.clipboard_thread.start()

    def stop(self):
        self._running = False
        self.batch_ready_event.set()  # Wake up the processor thread
        self.executor.shutdown(wait=True)
        self.conn.close()
        self.process(("disconnect", self.conn))
        # Trigger the clipboard thread to process the last command
        self.clipboard_queue.put("")
        # Trigger the processor thread to process the last batch
        self.message_queue.put("")
        self.processor_thread.join()
        self.clipboard_thread.join()

    def _handle(self):
        buffer = bytearray()
        while self._running:
            try:
                data = self.conn.recv(CHUNK_SIZE)
                if not data:
                    break
                buffer.extend(data)

                while END_DELIMITER.encode() in buffer or CHUNK_DELIMITER.encode() in buffer:
                    if END_DELIMITER.encode() in buffer:
                        pos = buffer.find(END_DELIMITER.encode())
                        batch = buffer[:pos]
                        buffer = buffer[pos + len(END_DELIMITER):]  # Remove the batch from the buffer

                        # Add the batch to the queue
                        self.message_queue.put(batch.decode())
                        self.batch_ready_event.set()
                    elif CHUNK_DELIMITER.encode() in buffer:
                        pos = buffer.find(CHUNK_DELIMITER.encode())
                        chunk = buffer[:pos]
                        buffer = buffer[pos + len(CHUNK_DELIMITER):]  # Remove the chunk from the buffer

                        # Add the chunk to the queue
                        self.message_queue.put(chunk.decode())
                        self.batch_ready_event.set()

            except Exception as e:
                self.logger(f"Error receiving data: {e}", 2)
                break
        self.stop()

    def _process_message_queue(self):
        batch_buffer = []

        while self._running:
            try:
                self.batch_ready_event.wait(BATCH_PROCESS_INTERVAL)

                while not self.message_queue.empty() and len(batch_buffer) < MAX_BATCH_SIZE:
                    batch_buffer.append(self.message_queue.get())

                if batch_buffer:
                    self._process_batch(batch_buffer)
                    batch_buffer.clear()

                self.batch_ready_event.clear()
            except Exception as e:
                self.logger(f"Error processing message queue: {e}", 2)

    def _process_clipboard_queue(self):
        while self._running:
            command = self.clipboard_queue.get()
            self._process(command)
            self.clipboard_queue.task_done()

    def _process_batch(self, batch_buffer):
        for command in batch_buffer:
            if "clipboard" in command:
                self.clipboard_queue.put(command)
            else:
                self.executor.submit(self._process, command)

    def _process(self, command):
        self.process(command)


class ClientCommandProcessor:
    def __init__(self, server):
        self.server = server
        self.logger = Logger.get_instance().log

    def process_client_command(self, command):
        command_handler = CommandFactory.create_command(command, self.server)
        if command_handler:
            command_handler.execute()
        else:
            self.logger(f"Invalid client command: {command}", Logger.ERROR)
