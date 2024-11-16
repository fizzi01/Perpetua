import ssl
import socket
import time
from abc import ABC, abstractmethod
from typing import Callable

from config.ServerConfig import Clients
from server.ClientHandler import ClientHandlerFactory
from utils.Logging import Logger


class SSLFactory:
    @staticmethod
    def create_ssl_socket(sock: socket.socket, certfile: str, keyfile: str) -> ssl.SSLSocket:
        context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        context.load_cert_chain(certfile=certfile, keyfile=keyfile)
        ssl_sock = context.wrap_socket(sock, server_side=True)
        return ssl_sock


class ConnectionHandler(ABC):
    INACTIVITY_TIMEOUT = 30

    def __init__(self,command_processor: Callable[[str | tuple], None] = None):
        self.log = Logger.get_instance().log
        self.client_handlers = []

        self.recent_activity = False
        self.last_check_time = time.time()

        self.command_processor = command_processor

    @abstractmethod
    def handle_connection(self, conn: socket.socket, addr: tuple, clients: Clients):
        pass

    def stop(self):
        for handler in self.client_handlers:
            handler.stop()

    def check_client_connections(self):
        current_time = time.time()
        if self.recent_activity or (
                current_time - self.last_check_time) > self.INACTIVITY_TIMEOUT:  # Check every 60 seconds if no activity
            for handler in self.client_handlers:
                try:
                    handler.conn.send(b'\x00')
                except (socket.error, ConnectionResetError):
                    self.log(f"Client {handler.address} disconnected.", Logger.WARNING)
                    self.command_processor(("disconnect", handler.conn))
                    handler.stop()
            self.recent_activity = False
            self.last_check_time = current_time

    def is_client_connected(self, address: tuple):
        for handler in self.client_handlers:
            if handler.address == address:
                return True
        return False


class ConnectionHandlerFactory:
    @staticmethod
    def create_handler(ssl_enabled: bool, ssl_factory: SSLFactory = None, certfile: str = None,
                       keyfile: str = None, command_processor: Callable[[str | tuple], None] = None) -> ConnectionHandler:
        if ssl_enabled:
            return SSLConnectionHandler(ssl_factory=ssl_factory, certfile=certfile, keyfile=keyfile, command_processor=command_processor)
        else:
            return NonSSLConnectionHandler(command_processor=command_processor)


class SSLConnectionHandler(ConnectionHandler):

    def __init__(self, ssl_factory: SSLFactory, certfile: str, keyfile: str, command_processor: Callable[[str | tuple], None] = None):
        super().__init__(command_processor=command_processor)
        self.ssl_factory = ssl_factory
        self.certfile = certfile
        self.keyfile = keyfile
        self.log = Logger.get_instance().log

    def handle_connection(self, conn: socket.socket, addr: tuple, clients: Clients):
        try:
            ssl_conn = self.ssl_factory.create_ssl_socket(conn, self.certfile, self.keyfile)
            self.log(f"SSL connection established with {addr}")

            # Exchange configuration info (this is a placeholder, implement your own logic)
            self.exchange_configuration(ssl_conn)

            # Add the SSL connection to the clients manager
            self.add_client_connection(ssl_conn, addr, clients)
        except Exception as e:
            self.log(f"Error handling connection from {addr}: {e}", Logger.ERROR)

    def exchange_configuration(self, ssl_conn: ssl.SSLSocket):
        # Implement your configuration exchange logic here
        pass

    def add_client_connection(self, ssl_conn: ssl.SSLSocket, addr: tuple, clients: Clients):
        for pos in clients.get_possible_positions():
            if clients.get_address(pos) == addr[0]:
                clients.set_connection(pos, ssl_conn)
                client_handler = ClientHandlerFactory.create_client_handler(ssl_conn, addr, self.command_processor)
                client_handler.start()
                self.client_handlers.append(client_handler)
                break


class NonSSLConnectionHandler(ConnectionHandler):

    def handle_connection(self, conn: socket.socket, addr: tuple, clients: Clients):
        try:
            self.log(f"Non-SSL connection established with {addr}")

            # Exchange configuration info (this is a placeholder, implement your own logic)
            self.exchange_configuration(conn)

            # Add the connection to the clients manager
            self.add_client_connection(conn, addr, clients)
        except Exception as e:
            self.log(f"Error handling connection from {addr}: {e}", Logger.ERROR)

    def exchange_configuration(self, conn: socket.socket):
        # Implement your configuration exchange logic here
        pass

    def add_client_connection(self, conn: socket.socket, addr: tuple, clients: Clients):
        for pos in clients.get_possible_positions():
            if clients.get_address(pos) == addr[0]:
                clients.set_connection(pos, conn)
                client_handler = ClientHandlerFactory.create_client_handler(conn, addr, self.command_processor)
                client_handler.start()
                self.client_handlers.append(client_handler)
                break


# Singleton per il socket del server
class ServerSocket:
    _instance = None

    def __new__(cls, host: str, port: int, wait: int):
        if cls._instance is None or not cls._instance.is_socket_open():
            cls._instance = super(ServerSocket, cls).__new__(cls)
            cls._instance._initialize_socket(host, port, wait)
        return cls._instance

    def _initialize_socket(self, host: str, port: int, wait: int):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.settimeout(wait)
        self.host = host
        self.port = port

    def bind_and_listen(self):
        self.socket.bind((self.host, self.port))
        self.socket.listen()

    def accept(self):
        return self.socket.accept()

    def pause(self):
        self.socket.detach()

    def close(self):
        self.socket.close()

    def is_socket_open(self):
        try:
            self.socket.getsockname()
            return True
        except socket.error:
            return False
