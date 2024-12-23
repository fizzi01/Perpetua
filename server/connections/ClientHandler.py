import socket
from threading import Thread, Event

from concurrent.futures import ThreadPoolExecutor
from queue import Queue
from typing import Callable

from utils.command.Command import CommandFactory
from utils.Interfaces import IBaseSocket, IClientHandler
from utils.Logging import Logger
from utils.net.netData import *
from utils.Interfaces import IClientCommandProcessor, IClientHandlerFactory

BATCH_PROCESS_INTERVAL = 0.01
MAX_BATCH_SIZE = 10
MAX_WORKERS = 10


class ClientHandlerFactory(IClientHandlerFactory):

    def create_handler(self, conn: IBaseSocket, screen: str,
                       process_command: Callable[[str | tuple, str], None]):
        return ClientHandler(client_socket=conn, screen=screen, command_processor=process_command)


class ClientHandler(IClientHandler):
    """
    Classe per la gestione di un client connesso al server.
    :param client_socket: Connessione del client
    :param process: process(command, screen) with 'command' as a tuple of elements
    """

    def __init__(self, client_socket: IBaseSocket | socket.socket, screen: str, command_processor: Callable[[str | tuple, str], None]):
        self.conn = client_socket
        self.address = self.conn.address
        self.screen = screen

        self.logger = Logger.get_instance().log
        self._running = False
        self.thread: Thread | None = None

        self.process = command_processor

        self.message_queue = Queue()
        self.clipboard_queue = Queue()
        self.batch_ready_event = Event()
        self.executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)

        self.processor_thread: Thread | None = None
        self.clipboard_thread: Thread | None = None

        self._thread_pool = []

    def start(self):
        self.thread = Thread(target=self._handle, daemon=True)
        self._running = True
        try:
            self.thread.start()
            self.logger(f"Client {self.address} connected.", 1)
        except Exception as e:
            self.logger(f"Failed to start client connection: {e}", 2)

        # Start thread to process messages in the queue
        self.processor_thread = Thread(target=self._process_message_queue, daemon=True)
        self._thread_pool.append(self.processor_thread)
        self.processor_thread.start()

        self.clipboard_thread = Thread(target=self._process_clipboard_queue, daemon=True)
        self._thread_pool.append(self.clipboard_thread)
        self.clipboard_thread.start()

    def stop(self):
        self._running = False
        self.batch_ready_event.set()  # Wake up the processor thread
        self.executor.shutdown()
        self.conn.close()
        self.process(("disconnect", self.conn), self.screen)
        # Trigger the clipboard thread to process the last command
        self.clipboard_queue.put("")
        # Trigger the processor thread to process the last batch
        self.message_queue.put("")

        for thread in self._thread_pool:
            thread.join()

    def is_alive(self) -> bool:
        if self.processor_thread and self.clipboard_thread:
            return self.processor_thread.is_alive() and self.clipboard_thread.is_alive()

    def _handle(self):
        buffer = bytearray()
        while self._running:
            try:
                data = self.conn.recv(CHUNK_SIZE)
                if not data or data == b'':
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
            except socket.timeout:
                continue
            except socket.error:
                break
            except Exception as e:
                # Controllo se il client ha chiuso la connessione
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
        self.process(command, self.screen)


class ClientCommandProcessor(IClientCommandProcessor):

    def process_client_command(self, command, screen):
        if not command:
            return
        command_handler = CommandFactory.create_command(raw_command=command, context=self.context,
                                                        message_service=self.message_service, event_bus=self.event_bus,
                                                        screen=screen)
        if command_handler:
            command_handler.screen = screen
            command_handler.execute()
        else:
            self.logger(f"Invalid client command: {command}", Logger.ERROR)
