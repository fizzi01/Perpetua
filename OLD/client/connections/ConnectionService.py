import threading
from socket import timeout as SocketTimeout, error as SocketError
import time

from utils.Logging import Logger
from utils.Interfaces import IServerConnectionService, \
    IClientSocket, IClientContext, IServerConnectionHandler


class ConnectionService(IServerConnectionService):

    CONNECTION_CHECK_TIMEOUT = 2
    LOOP_TIMEOUT = 0.5

    def __init__(self,
                 socket: IClientSocket,
                 connection_handler: IServerConnectionHandler,
                 context: IClientContext,
                 logger: Logger):

        super().__init__()

        self.socket = socket
        self.connection_handler = connection_handler
        self.context = context
        self.logger: Logger = logger

        self._running: bool = False
        self._connected: bool = False
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._is_main_running_event = threading.Event()

    def start(self) -> bool:
        try:
            self.logger.log(f"Client in listening.", Logger.INFO)
            self._running = True
            self._thread.start()

            self._is_main_running_event.wait(timeout=1)
            if not self._is_main_running_event.is_set():
                self.logger.log("ConnectionService not started properly.", Logger.ERROR)
                return False

            return True
        except Exception as e:
            self.logger.log(f"ConnectionService not started: {e}", Logger.ERROR)
            return False

    def join(self, timeout: float = 5):
        self._running = False

        if self._is_main_running_event.is_set():
            self._is_main_running_event.clear()
            if self._thread.is_alive():
                self._thread.join(timeout=timeout)

        self.socket.close()
        self.connection_handler.stop()

        self.logger.log("ConnectionService stopped.", Logger.DEBUG)

    def is_alive(self) -> bool:
        return self._thread.is_alive()

    def _loop(self):
        self._is_main_running_event.set()
        while self._running:
            try:
                if self._connected:
                    self._connected = self.connection_handler.check_server_connection()
                    time.sleep(self.CONNECTION_CHECK_TIMEOUT)
                    continue

                self._connected = self.connection_handler.handle_connection()
                time.sleep(self.LOOP_TIMEOUT)
            except SocketTimeout as e:
                if self._running:
                    self.logger.log("Connection timeout.", Logger.ERROR)
                    continue
                else:
                    break
            except SocketError as e:
                self.logger.log(f"{e}", 2)
                continue
            except Exception as e:
                self.logger.log(f"Error connecting to the server: {e}", 2)
                continue

        self.logger.log("Server listening stopped.", Logger.WARNING)
        self._is_main_running_event.clear()
