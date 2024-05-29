import socket
import threading
from collections.abc import Callable

from utils.netData import *


class ClientHandler:
    """
    Classe per la gestione di un client connesso al server.
    :param conn: Connessione del client
    :param address: Indirizzo del client
    :param process: Funzione di processamento del comando ricevuto
    :param on_disconnect: Funzione da chiamare alla disconnessione del client
    """

    def __init__(self, conn, address, process: Callable, on_disconnect: Callable, logger: Callable):
        self.buffer = ""

        self.conn = conn
        self.address = address
        self.process = process
        self.on_disconnect = on_disconnect
        self.logger = logger
        self._running = False
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self._handle, daemon=True)
        self._running = True
        try:
            self.thread.start()
            self.logger(f"Client {self.address} connected.", 1)
        except Exception as e:
            self.logger(f"Failed to start client connection: {e}", 2)

    """
    Chiude la connessione col client e termina la thread.
    """

    def stop(self):
        self._running = False
        self._cleanup()
        # self.thread.join()

    def _handle(self):
        temp_buffer = ""
        while self._running:
            try:
                data = self.conn.recv(1024).decode()
                if not data:
                    break
                self.buffer += data

                while END_DELIMITER in self.buffer or CHUNK_DELIMITER in self.buffer:
                    if END_DELIMITER in self.buffer:
                        # Find the first end delimiter
                        pos = self.buffer.find(END_DELIMITER)
                        # Extract the complete command
                        command = temp_buffer + self.buffer[:pos]
                        temp_buffer = ""  # Clear the temporary buffer

                        # Remove the command from the buffer
                        self.buffer = self.buffer[pos + len(END_DELIMITER):]  # Skip the length of END_DELIMITER
                        # Process the command
                        self._process(command)
                    elif CHUNK_DELIMITER in self.buffer:
                        # Find the first message end delimiter
                        pos = self.buffer.find(CHUNK_DELIMITER)
                        # Add the chunk to the temporary buffer
                        temp_buffer += self.buffer[:pos]

                        # Remove the chunk from the buffer
                        self.buffer = self.buffer[pos + len(CHUNK_DELIMITER):]  # Skip the length of CHUNK_DELIMITER

            except socket.error:
                if self._running:
                    self.logger(f"{self.address}: Socket error occurred.", 2)
                break

        if self._running:
            self._cleanup()
        return

    def _process(self, command):
        self.process(command)

    def _cleanup(self):
        self.conn.close()
        self.logger(f"Client {self.address} disconnected.", 1)
        self.on_disconnect(self.conn)
        return
