import socket
import threading
from collections.abc import Callable


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
        self.thread = threading.Thread(target=self._handle)
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
        #self.thread.join()

    def _handle(self):
        while self._running:
            try:
                buffer = ""

                data = self.conn.recv(1024).decode()
                if not data:
                    break
                buffer += data

                while '\n' in buffer:
                    # Trova il primo delimitatore di nuova riga
                    pos = buffer.find('\n')
                    # Estrai il comando completo
                    command = buffer[:pos]
                    # Rimuovi il comando dal buffer
                    buffer = buffer[pos + 1:]
                    # Processa il comando
                    self._process(command)

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
        self.logger(f"Client {self.address} disconnected.",1)
        self.on_disconnect(self.conn)
        return
