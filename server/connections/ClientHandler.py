import socket
from threading import Thread, Event

from concurrent.futures import ThreadPoolExecutor
from queue import Queue
from typing import Callable

from utils.command.Command import CommandFactory
from utils.Interfaces import IBaseSocket, IClientHandler
from utils.Logging import Logger
from utils.net.netData import *
from utils.net.ChunkManager import ChunkManager
from utils.Interfaces import IClientCommandProcessor, IClientHandlerFactory
from utils.protocol.adapter import ProtocolAdapter
from utils.protocol.ordering import OrderedMessageProcessor
from utils.protocol.message import ProtocolMessage
from utils.data import DataObjectFactory

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
        
        # Protocol support for ordered processing
        self.protocol_adapter = ProtocolAdapter()
        self.ordered_processor = OrderedMessageProcessor(
            process_callback=self._process_ordered_message,
            max_delay_tolerance=0.05  # 50ms tolerance for mouse smoothness
        )

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
        
        # Start ordered message processing
        self.ordered_processor.start()

    def stop(self):
        self._running = False
        self.batch_ready_event.set()  # Wake up the processor thread
        self.executor.shutdown()
        
        # Stop ordered message processing
        self.ordered_processor.stop()
        
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
        """Handle incoming data using new ProtocolMessage-level chunking."""
        chunk_manager = ChunkManager()
        data_buffer = b''
        
        while self._running:
            try:
                # Receive data from socket
                incoming_data = self.conn.recv(CHUNK_SIZE)
                if not incoming_data or incoming_data == b'':
                    break
                
                # Add to buffer
                data_buffer += incoming_data
                
                # Process complete messages from buffer
                complete_messages, bytes_consumed = chunk_manager.receive_data(data_buffer)
                
                # Remove processed data from buffer
                if bytes_consumed > 0:
                    data_buffer = data_buffer[bytes_consumed:]
                
                # Add complete messages to queue
                for complete_message in complete_messages:
                    if isinstance(complete_message, ProtocolMessage):
                        # Structured message - add to queue for ordered processing
                        self.message_queue.put(complete_message)
                    else:
                        # Legacy data - add as string
                        self.message_queue.put(str(complete_message))
                    
                    self.batch_ready_event.set()
                    
            except socket.timeout:
                continue
            except socket.error:
                break
            except Exception as e:
                # Check if client closed connection
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
            # Check if this is a structured message
            if isinstance(command, ProtocolMessage):
                try:
                    # Use ordered processing for mouse messages to ensure smoothness
                    if command.message_type == "mouse":
                        self.ordered_processor.add_message(command)
                    else:
                        # Process other message types immediately
                        self._process_ordered_message(command)
                except Exception as e:
                    self.logger(f"Error processing structured message: {e}", 2)
                    # Fallback to legacy processing
                    self.executor.submit(self._process, command)
            else:
                command_str = str(command)
                if command_str:
                    # Legacy command processing
                    self.executor.submit(self._process, command)

    def _process(self, command):
        self.process(command, self.screen)

    def _process_ordered_message(self, message: ProtocolMessage):
        """Process a structured message using DataObject approach."""
        try:
            # Convert ProtocolMessage to DataObject instead of legacy format
            data_object = DataObjectFactory.create_from_protocol_message(message)
            if data_object:
                # Use DataObject-based processing
                self.process_with_data_object(data_object, self.screen)
            else:
                # Fallback to legacy processing if DataObject creation fails
                legacy_command = self.protocol_adapter.structured_to_legacy(message)
                if legacy_command:
                    self.process(legacy_command, self.screen)
        except Exception as e:
            self.logger(f"Error processing structured message: {e}", 2)
    
    def process_with_data_object(self, data_object, screen):
        """Process command using structured data object."""
        try:
            # Call the command processor with the data object
            if callable(self.process):
                # Check if we can update the process function to accept data_object
                self.process(data_object=data_object, screen=screen)
            else:
                self.logger(f"Cannot process data object directly, using legacy fallback", Logger.DEBUG)
        except Exception as e:
            self.logger(f"Error processing command with data object: {e}", 2)


class ClientCommandProcessor(IClientCommandProcessor):

    def process_client_command(self, command=None, screen=None, data_object=None):
        """
        Process client command using either legacy command or structured data object.
        
        Args:
            command: Legacy command string/tuple (optional)
            screen: Screen identifier
            data_object: Structured data object (preferred approach)
        """
        if not command and not data_object:
            return
            
        command_handler = CommandFactory.create_command(
            raw_command=command, context=self.context,
            message_service=self.message_service, event_bus=self.event_bus,
            screen=screen, data_object=data_object
        )
        
        if command_handler:
            command_handler.screen = screen
            command_handler.execute()
        else:
            self.logger(f"Invalid client command: {command}", Logger.ERROR)
