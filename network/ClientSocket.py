import socket
import ssl
import time
from threading import Lock
from abc import ABC, abstractmethod
from typing import Callable

from client.ServerHandler import ServerHandlerFactory
from utils.Logging import Logger


class ClientSocket:
    _instance = None
    _lock = Lock()

    def __new__(cls, host: str, port: int, wait: int, use_ssl: bool = False, certfile: str = None, keyfile: str = None):
        if cls._instance is None or not cls._instance.is_socket_open():
            with cls._lock:
                if cls._instance is None or not cls._instance.is_socket_open():
                    cls._instance = super(ClientSocket, cls).__new__(cls)
                    cls._instance._initialize_socket(host, port, wait, use_ssl, certfile, keyfile)
        return cls._instance

    def _initialize_socket(self, host: str, port: int, wait: int, use_ssl: bool, certfile: str, keyfile: str):
        self.host = host
        self.port = port
        self.use_ssl = use_ssl
        self.certfile = certfile
        self.keyfile = keyfile
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.settimeout(wait)

    def connect(self):
        if self.use_ssl:
            context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
            context.load_cert_chain(certfile=self.certfile, keyfile=self.keyfile)
            self.socket = context.wrap_socket(self.socket, server_hostname=self.host)
        self.socket.connect((self.host, self.port))

    def close(self):
        self.socket.close()

    def is_socket_open(self):
        try:
            self.socket.getsockname()
            return True
        except socket.error:
            return False


class ConnectionHandler(ABC):
    INACTIVITY_TIMEOUT = 30

    def __init__(self, client_socket: ClientSocket, command_processor: Callable):
        self.client_socket = client_socket
        self.command_processor = command_processor
        self.server_handler = None

        self.recent_activity = False
        self.last_check_time = time.time()

        self.logger = Logger.get_instance().log

    @abstractmethod
    def handle_connection(self):
        pass

    @abstractmethod
    def add_server_connection(self):
        pass

    def stop(self):
        if self.server_handler is not None:
            self.server_handler.stop()
            self.server_handler = None
        self.client_socket.close()

    def check_server_connection(self):
        current_time = time.time()
        if self.recent_activity or (
                current_time - self.last_check_time) > self.INACTIVITY_TIMEOUT:  # Check every 60 seconds if no activity
            if self.server_handler is not None and not self.server_handler.is_socket_open():
                self.logger("Server connection closed.", Logger.WARNING)
                self.server_handler.stop()
                self.server_handler = None

            self.recent_activity = False
            self.last_check_time = current_time

        if self.server_handler is None:
            self.logger("Server connection lost.", Logger.WARNING)
            return False

        return True


class SSLConnectionHandler(ConnectionHandler):
    def handle_connection(self):
        try:
            self.client_socket.connect()
            self.add_server_connection()
            self.logger("SSL connection established.")
            return True
        except Exception as e:
            self.logger(f"Error establishing SSL connection: {e}", Logger.ERROR)
            return False

    def add_server_connection(self):
        self.server_handler = ServerHandlerFactory.create_server_handler(self.client_socket.socket, self.command_processor)
        self.server_handler.start()


class NonSSLConnectionHandler(ConnectionHandler):
    def handle_connection(self):
        try:
            self.client_socket.connect()
            self.add_server_connection()
            self.logger("Non-SSL connection established.")
            return True
        except Exception as e:
            self.logger(f"Error establishing non-SSL connection: {e}", Logger.ERROR)
            return False

    def add_server_connection(self):
        self.server_handler = ServerHandlerFactory.create_server_handler(self.client_socket.socket,
                                                                         self.command_processor)
        self.server_handler.start()


class ConnectionHandlerFactory:
    @staticmethod
    def create_handler(client_socket: ClientSocket, command_processor: Callable ) -> ConnectionHandler:
        if client_socket.use_ssl:
            return SSLConnectionHandler(client_socket, command_processor)
        else:
            return NonSSLConnectionHandler(client_socket, command_processor)
