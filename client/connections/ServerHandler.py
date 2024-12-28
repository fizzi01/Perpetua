import socket
import threading
from collections.abc import Callable
from time import sleep, time
from queue import Queue, Empty

from utils.Interfaces import IServerHandler, IClientSocket, IServerHandlerFactory
from utils.Logging import Logger
from utils.net.netData import *


class ServerHandlerFactory(IServerHandlerFactory):

    def create_handler(self, conn: IClientSocket,
                       process_command: Callable[[str], None]) -> IServerHandler:
        return ServerHandler(connection=conn, command_func=process_command)


class ServerHandler(IServerHandler):
    """
    Class to handle server commands received from the client.
    :param connection: Client connection
    :param command_func: Function to process commands
    """

    BATCH_PROCESS_INTERVAL = 0.0000001
    TIMEOUT = 0.0000001

    CHUNK_DELIMITER = CHUNK_DELIMITER.encode()
    END_DELIMITER = END_DELIMITER.encode()
    CMD_DELIMITER = CMD.encode()

    def __init__(self, connection: IClientSocket, command_func: Callable[[str], None]):
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
            if self.main_thread.is_alive():
                self.main_thread.join()

        if self.buffer_thread.is_alive():
            self.buffer_thread.join()

    def start(self):
        self._running = True
        try:
            self.start_threads()
            self.log("Client in listening mode.", Logger.DEBUG)
        except Exception as e:
            self.log(f"Error starting client: {e}", Logger.ERROR)

    def stop(self):
        self.stop_threads()
        self.log("Client disconnected.", Logger.WARNING)
        self.conn.close()

    def _handle_server_commands(self):
        """Handle commands continuously received from the server."""
        while self._running:
            try:
                data = self.conn.recv(CHUNK_SIZE)

                if not data:
                    break

                self.data_queue.put(data)
            except socket.timeout:
                sleep(self.TIMEOUT)
                continue
            except Exception as e:
                self.log(f"Error receiving data: {e}", 2)
                break
        self.stop()

    def _check_chunk_batch(self, data: bytes):
        """
        Check if the chunk delimiter seperates a single command data. If not data should be split.
        :param data: Data to check
        :return: Data split into batches or the input data
        """
        # Split by CHUNK_DELIMITER
        if self.CHUNK_DELIMITER not in data:
            return [data]

        chunks = data.split(self.CHUNK_DELIMITER)
        # If the last chunk has 2 or more CMD_DELIMITER, it means the last chunk is not a complete command
        if chunks[-1].count(self.CMD_DELIMITER) > 1:
            # If so, reuturn all chunks
            return chunks

        # Return data as a single batch
        return [data]

    def _buffer_and_process_batches(self):
        """Buffer data and process batches in a separate thread."""
        buffer = bytearray()
        last_batch_time = 0
        while self._running:
            try:
                while not self.data_queue.empty() or time() - last_batch_time < self.BATCH_PROCESS_INTERVAL:
                    buffer.extend(self.data_queue.get())

                while self.END_DELIMITER in buffer:
                    end_pos = buffer.find(self.END_DELIMITER)
                    batch = buffer[:end_pos]
                    buffer = buffer[end_pos + len(self.END_DELIMITER):]

                    # Check if the batch is a single command
                    clean_batch = self._check_chunk_batch(batch)

                    # Process each batch
                    for batch in clean_batch:
                        # Remove CHUNK_DELIMITER from the batch
                        batch = batch.replace(self.CHUNK_DELIMITER, b'')

                        self._process_batch(batch.decode())
                sleep(self.TIMEOUT)
            except Empty:
                sleep(self.TIMEOUT)
                continue
            except Exception as e:
                self.log(f"Error processing data: {e}", Logger.ERROR)

    def _process_batch(self, command):
        self.process_command(command)
