import ssl
import socket
import time
import uuid
from typing import Callable

from zeroconf import ServiceInfo, Zeroconf, ServiceStateChange, ServiceBrowser

from utils import screen_size
from utils.net import netConstants, NetUtils
from utils.Interfaces import IBaseSocket, IClientConnectionHandler, IServerSocket, IClientHandlerFactory, \
    IConnectionHandlerFactory
from utils.Logging import Logger

from config import SERVICE_NAME
from config.ServerConfig import Clients


class SSLFactory:
    @staticmethod
    def create_ssl_socket(sock: socket.socket, certfile: str, keyfile: str) -> ssl.SSLSocket:
        context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        context.load_cert_chain(certfile=certfile, keyfile=keyfile)
        ssl_sock = context.wrap_socket(sock, server_side=True)
        return ssl_sock


class ConnectionHandler(IClientConnectionHandler):
    INACTIVITY_TIMEOUT = 3

    def __init__(self, process_command: Callable[[str | tuple, str], None],
                 client_handler_factory: IClientHandlerFactory):
        self.log = Logger.get_instance().log
        self.client_handlers = []

        self.recent_activity = False
        self.last_check_time = time.time()

        self.command_processor = process_command
        self.client_handler_factory = client_handler_factory

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
                    if not handler.conn.is_socket_open():
                        raise ConnectionResetError
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


class ConnectionHandlerFactory(IConnectionHandlerFactory):
    @staticmethod
    def create_handler(ssl_enabled: bool, certfile: str = None,
                       keyfile: str = None,
                       context=None,  # Not used by server
                       handler_socket=None,     # Not used by server
                       command_processor: Callable[[str | tuple, str], None] = None,
                       handler_factory: IClientHandlerFactory = None) -> ConnectionHandler:
        if ssl_enabled:
            return SSLConnectionHandler(certfile=certfile, keyfile=keyfile, process_command=command_processor,
                                        client_handler_factory=handler_factory)
        else:
            return NonSSLConnectionHandler(process_command=command_processor, client_handler_factory=handler_factory)


class SSLConnectionHandler(ConnectionHandler):

    def __init__(self, certfile: str, keyfile: str, process_command: Callable[[str | tuple, str], None] = None,
                 client_handler_factory: IClientHandlerFactory = None):
        super().__init__(process_command=process_command, client_handler_factory=client_handler_factory)
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
            ssl_conn = BaseSocket(ssl_conn, address=addr[0])

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
                client_handler = self.client_handler_factory.create_handler(conn=ssl_conn, screen=pos,
                                                                            process_command=self.command_processor)
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
                client_handler = self.client_handler_factory.create_handler(conn=conn, screen=pos,
                                                                            process_command=self.command_processor)
                client_handler.start()
                self.client_handlers.append(client_handler)
                break


class ServerSocket(IServerSocket):
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

    def get_host(self):
        return self.host

    def get_port(self):
        return self.port

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
                if info and info.properties:
                    try:
                        properties = {key.decode(): value.decode() for key, value in info.properties.items()}
                        if properties.get("app_name") == SERVICE_NAME and info.port == port:
                            conflict_found = True
                    except AttributeError:
                        pass

        browser = ServiceBrowser(self.zeroconf, "_http._tcp.local.", handlers=[on_service_state_change])
        time.sleep(1)  # Aspetta che i servizi vengano scoperti
        browser.cancel()
        return conflict_found

    def _register_mdns_service(self):
        self.host = NetUtils.get_local_ip()
        if not self.host:
            raise Exception("No connection.")
        service_info = ServiceInfo(
            "_http._tcp.local.",  # Tipo del servizio
            f"{SERVICE_NAME}-{uuid.uuid4().hex[:8]}._http._tcp.local.",
            addresses=[socket.inet_aton(self.host)],
            port=self.port,
            properties={"app_name": SERVICE_NAME},
            server=f"{SERVICE_NAME}.local.",
        )
        self.zeroconf.register_service(service_info)
        self.log(f"[mDNS] Server service {service_info.name} registered", Logger.DEBUG)

    def _unregister_mdns_service(self):
        self.zeroconf.unregister_all_services()
        self.zeroconf.close()
        self.log(f"[mDNS] Server service {SERVICE_NAME} unregistered.", Logger.DEBUG)


class BaseSocket(IBaseSocket):
    # Maschera il socket di base per consentire l'accesso ai metodi di socket
    def __getattr__(self, item):
        return getattr(self.socket, item)

    def __init__(self, sock: socket.socket, address: str = ""):
        self.socket = sock
        self._address = address
        self.log = Logger.get_instance().log

    @property
    def address(self) -> str:
        return self._address

    def send(self, data: str | bytes):
        if isinstance(data, str):
            data = data.encode()

        try:
            self.socket.sendall(data)
        except EOFError:
            pass

    def recv(self, size: int) -> bytes:
        return self.socket.recv(size)

    def close(self):
        try:
            self.socket.close()
        except EOFError:
            pass
        except ConnectionResetError:
            pass
        except BrokenPipeError:
            pass
        except OSError:
            pass
        except ConnectionAbortedError:
            pass

    def is_socket_open(self):
        try:
            self.socket.getpeername()
            return True
        except socket.error:
            return False
