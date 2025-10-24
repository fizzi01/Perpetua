import threading
from socket import timeout

from utils.Logging import Logger
from utils.Interfaces import IServerContext, IClients, IServerSocket, IClientConnectionHandler, IServerConnectionService


class ConnectionService(IServerConnectionService):
    def __init__(self,
                 server_socket: IServerSocket,
                 clients: IClients,
                 connection_handler: IClientConnectionHandler,
                 context: IServerContext,
                 logger: Logger):

        super().__init__()

        self.socket: IServerSocket = server_socket
        self.clients: IClients = clients
        self.connection_handler: IClientConnectionHandler = connection_handler
        self.context: IServerContext = context
        self.logger: Logger = logger

        self._running: bool = False
        self._thread = threading.Thread(target=self._accept_clients_loop, daemon=True)
        self._is_main_running_event = threading.Event()

    def start(self) -> bool:
        try:
            self.socket.bind_and_listen()
            self.logger.log(f"Server listening on {self.socket.get_host()}:{self.socket.get_port()}", Logger.INFO)
            self._running = True
            self._thread.start()
            self._is_main_running_event.wait(timeout=1)
            if not self._is_main_running_event.is_set():
                self.logger.log("ConnectionService not started properly.", Logger.ERROR)
                return False
            self._is_main_running_event.clear()
            return True
        except Exception as e:
            self.logger.log(f"ConnectionService not started: {e}", Logger.ERROR)
            return False

    def join(self, timeout: float = 5):
        self._running = False
        self.socket.close()
        self._is_main_running_event.set()  # sblocca eventuali attese
        self._thread.join(timeout=timeout)
        self.connection_handler.stop()
        self.logger.log("ConnectionService stopped.", Logger.DEBUG)

    def is_alive(self) -> bool:
        return self._thread.is_alive()

    def _accept_clients_loop(self):
        self._is_main_running_event.set()
        while self._running:
            try:
                conn, addr = self.socket.accept()
                self.logger.log(f"Client handshake from {addr[0]}", Logger.INFO)

                # Check if client already connected
                if self.connection_handler.is_client_connected(addr):
                    self.logger.log(f"Client {addr[0]} already connected.", Logger.WARNING)
                    conn.close()
                    continue

                self.connection_handler.handle_connection(conn, addr, self.clients)

            except timeout:
                # Controllo periodico stato connessioni
                if self._running:
                    self.connection_handler.check_client_connections()
                    continue
                else:
                    break
            except Exception as e:
                if self._running:
                    self.logger.log(f"{e}", Logger.ERROR)
                    continue
                else:
                    break

        self.logger.log("Server listening stopped.", Logger.WARNING)
        self._is_main_running_event.clear()  # segna thread terminato
