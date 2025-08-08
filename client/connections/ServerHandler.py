import socket
import threading
from collections.abc import Callable
from time import sleep, time
from queue import Queue, Empty
from typing import Any, Optional

from utils.Interfaces import IServerHandler, IClientSocket, IServerHandlerFactory
from utils.Logging import Logger
from utils.net.netData import *
from utils.net.ChunkManager import ChunkManager
from utils.protocol.adapter import ProtocolAdapter
from utils.protocol.ordering import OrderedMessageProcessor
from utils.protocol.message import ProtocolMessage
from utils.data import DataObjectFactory


class ServerHandlerFactory(IServerHandlerFactory):

    def create_handler(self, conn: IClientSocket,
                       process_command: Callable[[str, Any, Any], None]) -> IServerHandler:
        return ServerHandler(connection=conn, command_func=process_command)


class ServerHandler(IServerHandler):
    """
    Class to handle server commands received from the client.
    :param connection: Client connection
    :param command_func: Function to process commands
    """

    BATCH_PROCESS_INTERVAL = 0.0000001
    TIMEOUT = 0.0000001

    def __init__(self, connection: IClientSocket, command_func: Callable[[Optional[str], Any, Any], None]):
        self.conn = connection
        self.process_command = command_func
        self.on_disconnect = None
        self.log = Logger.get_instance().log
        self._running = False

        self.main_thread = None
        self.buffer_thread = None
        self.init_threads()

        self.data_queue = Queue()
        
        # Protocol support for ordered processing
        self.protocol_adapter = ProtocolAdapter()
        self.chunk_manager = ChunkManager()
        self.ordered_processor = OrderedMessageProcessor(
            process_callback=self._process_ordered_message,
            max_delay_tolerance=0.05  # 50ms tolerance for mouse smoothness
        )

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
            if self.main_thread.is_alive():
                self.main_thread.join()

        if self.buffer_thread.is_alive():
            self.buffer_thread.join()

    def start(self):
        self._running = True
        try:
            self.start_threads()
            self.ordered_processor.start()  # Start ordered message processing
            self.log("Client in listening mode.", Logger.DEBUG)
        except Exception as e:
            self.log(f"Error starting client: {e}", Logger.ERROR)

    def stop(self):
        self.stop_threads()
        self.ordered_processor.stop()  # Stop ordered message processing
        self.log("Client disconnected.", Logger.WARNING)
        self.conn.close()

    def _handle_server_commands(self):
        """Handle commands continuously received from the server using new ProtocolMessage-level chunking."""
        data_buffer = b''
        
        while self._running:
            try:
                # Receive data from socket
                incoming_data = self.conn.recv(CHUNK_SIZE)

                if not incoming_data:
                    break

                # Add to buffer
                data_buffer += incoming_data
                
                # Process complete messages from buffer
                complete_messages, bytes_consumed = self.chunk_manager.receive_data(data_buffer)
                
                # Remove processed data from buffer
                if bytes_consumed > 0:
                    data_buffer = data_buffer[bytes_consumed:]
                
                # Add complete messages to queue
                for complete_message in complete_messages:
                    self.data_queue.put(complete_message)
                    
            except socket.timeout:
                sleep(self.TIMEOUT)
                continue
            except Exception as e:
                self.log(f"Error receiving data: {e}", 2)
                break
        self.stop()

    def _buffer_and_process_batches(self):
        """Process complete messages from the data queue."""
        while self._running:
            try:
                # Get complete message from queue
                message = self.data_queue.get(timeout=self.BATCH_PROCESS_INTERVAL)
                
                if isinstance(message, ProtocolMessage):
                    # Structured message - use ordered processing for mouse events
                    if message.message_type == "mouse":
                        self.ordered_processor.add_message(message)
                    else:
                        # Process other message types immediately
                        self._process_ordered_message(message)
                else:
                    # Legacy string message
                    command_str = str(message)
                    if command_str:  # Skip empty messages
                        self._process_legacy_batch(command_str)
                        
            except Empty:
                sleep(self.TIMEOUT)
                continue
            except Exception as e:
                self.log(f"Error processing data: {e}", Logger.ERROR)

    def _process_legacy_batch(self, batch_data: str):
        """Process legacy batch data that may contain multiple commands."""
        # Handle batched legacy commands separated by '|'
        if '|' in batch_data:
            commands = batch_data.split('|')
            for command in commands:
                if command.strip():
                    self.process_command(command.strip(), None, None)
        else:
            # Single command
            self.process_command(batch_data, None, None)
    
    def _process_ordered_message(self, message: ProtocolMessage):
        """Process a structured message using DataObject approach."""
        try:
            # Convert ProtocolMessage to DataObject instead of legacy format
            data_object = DataObjectFactory.create_from_protocol_message(message)
            if data_object:
                # Use DataObject-based processing
                self.process_command_with_data_object(data_object)
            else:
                # Fallback to legacy processing if DataObject creation fails
                legacy_command = self.protocol_adapter.structured_to_legacy(message)
                if legacy_command:
                    self.process_command(legacy_command, None, None)
        except Exception as e:
            self.log(f"Error processing structured message: {e}", Logger.ERROR)
    
    def process_command_with_data_object(self, data_object):
        """Process command using structured data object."""
        try:
            # Call the command processor with the data object
            if hasattr(self.process_command, '__func__'):
                # If process_command is bound method, call it with data_object parameter
                self.process_command(None, None, data_object)
            else:
                # Fallback to legacy string conversion if needed
                self.log(f"Using legacy fallback for data object: {data_object.data_type}", Logger.DEBUG)
                # This should be replaced with direct DataObject processing
        except Exception as e:
            self.log(f"Error processing command with data object: {e}", Logger.ERROR)
