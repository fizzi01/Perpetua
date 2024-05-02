import socket
import threading
from collections.abc import Callable

class ClientHandler:

    """
    Classe per la gestione di un client connesso al server.
    @param conn: Connessione del client
    @param address: Indirizzo del client
    @param process: Funzione di processamento del comando ricevuto
    """
    def __init__(self, conn, address, process: Callable, on_disconnect: Callable):
        self.buffer = ""
        self.conn = conn
        self.address = address
        self.process = process
        self.on_disconnect = on_disconnect
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self._handle)
        self.thread.start()
        print(f"Client {self.address} connected.")

    def join(self):
        self.thread.join()
        self._cleanup()

    def _handle(self):
        while True:
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
                print(f"{self.address}: Socket error occurred.")
                break

        self._cleanup()

    def _process(self, command):
        self.process(command)

    def _cleanup(self):
        self.conn.close()
        print(f"Client {self.address} disconnected.")
        self.on_disconnect(self.conn)
        return
