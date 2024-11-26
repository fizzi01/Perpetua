import ssl
import socket
import time
import uuid
from abc import ABC, abstractmethod
from typing import Callable

from config import SERVICE_NAME
from config.ServerConfig import Clients
from server.ClientHandler import ClientHandlerFactory
from utils.Logging import Logger

from zeroconf import ServiceInfo, Zeroconf, ServiceStateChange, ServiceBrowser
from utils import net, netConstants, screen_size

class SSLFactory:
    @staticmethod
    def create_ssl_socket(sock: socket.socket, certfile: str, keyfile: str) -> ssl.SSLSocket:
        context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        context.load_cert_chain(certfile=certfile, keyfile=keyfile)
        ssl_sock = context.wrap_socket(sock, server_side=True)
        return ssl_sock


class ConnectionHandler(ABC):
    INACTIVITY_TIMEOUT = 3

    def __init__(self, command_processor: Callable[[str | tuple], None] = None, server_info: dict = None):
        self.log = Logger.get_instance().log
        self.client_handlers = []

        self.server_info = None

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
                    handler.stop()
                    self.client_handlers.remove(handler)
            self.recent_activity = False
            self.last_check_time = current_time

    def is_client_connected(self, address: tuple):
        for handler in self.client_handlers:
            if handler.address[0] == address[0]:  # Check only the IP address
                return True
        return False

    def exchange_configuration(self, conn: socket.socket):
        try:
            # Implement your configuration exchange logic here
            conn.send(netConstants.SCREEN_CONFIG_EXCHANGE_COMMAND.encode())
            screen_resolution = conn.recv(1024).decode()
            self.log(f"Client screen resolution: {screen_resolution}")
            # Extract the screen resolution from the client
            screen_width, screen_height = screen_resolution.split("x")
            screen_resolution = (int(screen_width), int(screen_height))

            # Invia al client la risoluzione dello schermo del server
            conn.send(f"{screen_size()[0]}x{screen_size()[1]}".encode())

            return {"screen_resolution": screen_resolution}
        except Exception as e:
            self.log(f"Error during configuration exchange: {e}", Logger.ERROR)
            return None


class ConnectionHandlerFactory:
    @staticmethod
    def create_handler(ssl_enabled: bool, certfile: str = None,
                       keyfile: str = None,
                       command_processor: Callable[[str | tuple], None] = None) -> ConnectionHandler:
        if ssl_enabled:
            return SSLConnectionHandler(certfile=certfile, keyfile=keyfile, command_processor=command_processor)
        else:
            return NonSSLConnectionHandler(command_processor=command_processor)


class SSLConnectionHandler(ConnectionHandler):

    def __init__(self, certfile: str, keyfile: str, command_processor: Callable[[str | tuple], None] = None):
        super().__init__(command_processor=command_processor)
        self.ssl_factory = SSLFactory()
        self.certfile = certfile
        self.keyfile = keyfile
        self.log = Logger.get_instance().log

    def handle_connection(self, conn: socket.socket, addr: tuple, clients: Clients):
        try:
            ssl_conn = self.ssl_factory.create_ssl_socket(conn, self.certfile, self.keyfile)
            self.log(f"SSL connection established with {addr}")

            # Exchange configuration info (this is a placeholder, implement your own logic)
            client_config = self.exchange_configuration(ssl_conn)
            if not client_config:
                self.log(f"Configuration exchange failed with {addr}", Logger.ERROR)
                conn.close()
                return

            # Wrap socket with BaseSocket
            ssl_conn = BaseSocket(ssl_conn)

            # Add the SSL connection to the clients manager
            self.add_client_connection(ssl_conn, addr, clients, client_config)
        except Exception as e:
            self.log(f"Error handling connection from {addr}: {e}", Logger.ERROR)

    def add_client_connection(self, ssl_conn, addr: tuple, clients: Clients, client_config: dict):

        if not clients.get_possible_positions():
            ssl_conn.close()
            self.log(f"Client {addr} rejected: Not allowed.", Logger.WARNING)

        for pos in clients.get_possible_positions():
            if clients.get_address(pos) == addr[0]:
                clients.set_connection(pos, ssl_conn)
                clients.set_screen_size(pos, client_config["screen_resolution"])
                client_handler = ClientHandlerFactory.create_client_handler(ssl_conn, addr, pos, self.command_processor)
                client_handler.start()
                self.client_handlers.append(client_handler)
                return

        # If the client is not allowed, close the connection
        ssl_conn.close()
        self.log(f"Client {addr} rejected: Max clients reached.", Logger.WARNING)


class NonSSLConnectionHandler(ConnectionHandler):

    def handle_connection(self, conn: socket.socket, addr: tuple, clients: Clients):
        try:
            self.log(f"Non-SSL connection established with {addr}")

            # Exchange configuration info (this is a placeholder, implement your own logic)
            client_config = self.exchange_configuration(conn)
            if not client_config:
                self.log(f"Configuration exchange failed with {addr}", Logger.ERROR)
                conn.close()
                return

            conn = BaseSocket(conn)

            # Add the connection to the clients manager
            self.add_client_connection(conn, addr, clients, client_config)
        except Exception as e:
            self.log(f"Error handling connection from {addr}: {e}", Logger.ERROR)

    def add_client_connection(self, conn, addr: tuple, clients: Clients, client_config: dict):
        for pos in clients.get_possible_positions():
            if clients.get_address(pos) == addr[0]:
                clients.set_connection(pos, conn)
                clients.set_screen_size(pos, client_config["screen_resolution"])
                client_handler = ClientHandlerFactory.create_client_handler(conn, addr, pos, self.command_processor)
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

    def _initialize_socket(self, host: str = "", port: int = 5001, wait: int = 5):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.settimeout(wait)
        self.host = host
        self.port = port

        self.log = Logger.get_instance().log

        self.zeroconf = Zeroconf()
        self._resolve_port_conflict()
        self._register_mdns_service()

    def bind_and_listen(self):
        self.socket.bind((self.host, self.port))
        self.socket.listen()

    def accept(self):
        return self.socket.accept()

    def pause(self):
        self.socket.detach()

    def close(self):
        self._unregister_mdns_service()
        self.socket.close()

    def is_socket_open(self):
        try:
            self.socket.getsockname()
            return True
        except socket.error:
            return False

    def _resolve_port_conflict(self):
        """Controlla se esiste un conflitto sulla porta con altri servizi mDNS."""
        while self._is_port_in_use_by_mdns(self.port):
            self.log(f"[mDNS] Port {self.port} is already in use. Trying next port...", Logger.WARNING)
            self.port += 1

    def _is_port_in_use_by_mdns(self, port):
        """Cerca altri servizi mDNS con la stessa porta."""
        conflict_found = False

        def on_service_state_change(zeroconf, service_type, name, state_change):
            nonlocal conflict_found
            if state_change == ServiceStateChange.Added:
                info = zeroconf.get_service_info(service_type, name)
                if info:
                    properties = {key.decode(): value.decode() for key, value in info.properties.items()}
                    if properties.get("app_name") == SERVICE_NAME and info.port == port:
                        conflict_found = True

        browser = ServiceBrowser(self.zeroconf, f"_http._tcp.local.", handlers=[on_service_state_change])
        time.sleep(1)  # Aspetta che i servizi vengano scoperti
        browser.cancel()
        return conflict_found

    def _register_mdns_service(self):
        self.host = net.get_local_ip()
        service_info = ServiceInfo(
            "_http._tcp.local.",  # Tipo del servizio
            f"{SERVICE_NAME}-{uuid.uuid4().hex[:8]}._http._tcp.local.",
            addresses=[socket.inet_aton(self.host)],
            port=self.port,
            properties={"app_name": SERVICE_NAME},
            server=f"{SERVICE_NAME}.local.",
        )
        self.zeroconf.register_service(service_info)
        self.log(f"[mDNS] Server {service_info.name} registered", Logger.DEBUG)

    def _unregister_mdns_service(self):
        self.zeroconf.unregister_all_services()
        self.zeroconf.close()
        self.log(f"[mDNS] Service {SERVICE_NAME} unregistered.", Logger.DEBUG)


class BaseSocket:
    # Maschera il socket di base per consentire l'accesso ai metodi di socket
    def __getattr__(self, item):
        return getattr(self.socket, item)

    def __init__(self, socket: socket.socket):
        self.socket = socket
        self.log = Logger.get_instance().log

    def send(self, data: str | bytes):
        if isinstance(data, str):
            data = data.encode()

        try:
            self.socket.send(data)
        except EOFError:
            pass

    def recv(self, size: int) -> bytes:
        return self.socket.recv(size)

    def close(self):
        try:
            self.socket.close()
        except Exception as e:
            pass

    def is_socket_open(self):
        try:
            self.socket.getpeername()
            return True
        except socket.error:
            return False
