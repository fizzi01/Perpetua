import socket
import ssl
import time
from threading import Lock
from abc import abstractmethod
from typing import Callable, Optional

from config import SERVICE_NAME
from network.exceptions import ServerNotFoundException
from utils.net import netConstants
from utils.Interfaces import IServerHandlerFactory
from utils.Logging import Logger
from utils.Interfaces import IClientSocket, IServerConnectionHandler, IConnectionHandlerFactory, \
    IClientContext

from zeroconf import Zeroconf, ServiceBrowser, ServiceStateChange


class ClientSocket(IClientSocket):
    _instance = None
    _lock = Lock()
    ssl_socket = None
    socket = None

    def __new__(cls, host: str, port: int, wait: int, use_ssl: bool = False, certfile: str = None):
        if cls._instance is None or not cls._instance.is_socket_open():
            with cls._lock:
                if cls._instance is None or not cls._instance.is_socket_open():
                    cls._instance = super(ClientSocket, cls).__new__(cls)
                    cls._instance._initialize_socket(host, port, wait, use_ssl, certfile)
        return cls._instance

    def _initialize_socket(self, host: str, port: int, wait: int, use_ssl: bool, certfile: str):
        self.host = host
        self.port = port
        self.wait = wait
        self.use_ssl = use_ssl
        self.certfile = certfile
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.ssl_socket = None
        # self.socket.settimeout(wait)

        self.use_discovery = True if len(self.host) == 0 else False
        self.log = Logger.get_instance().log

    def _discover_server(self):
        service_found = False

        def on_service_state_change(zeroconf, service_type, name, state_change):
            nonlocal service_found
            if state_change == ServiceStateChange.Added and not service_found:
                info = zeroconf.get_service_info(service_type, name)
                if info and info.properties:
                    try:
                        properties = {key.decode(): value.decode() for key, value in info.properties.items()}
                        if properties.get("app_name") == SERVICE_NAME and info.port == self.port:
                            self.host = socket.inet_ntoa(info.addresses[0])
                            self.port = info.port
                            self.log(f"[mDNS] Resolved server to {self.host}:{self.port}")
                            service_found = True
                    except AttributeError as e:
                        pass

        zeroconf = Zeroconf()
        browser = ServiceBrowser(zeroconf, "_http._tcp.local.", handlers=[on_service_state_change])
        self.log("[mDNS] Searching for service ...", Logger.DEBUG)
        time.sleep(2)  # Attendi per completare la scoperta
        browser.cancel()
        zeroconf.close()

        if not self.host or not self.port:
            raise ServerNotFoundException("No matching server found.")

    def connect(self):
        if self.use_discovery:
            self._discover_server()

        if self.use_ssl:
            context = ssl.create_default_context()
            context.load_verify_locations(cafile=self.certfile)
            self.ssl_socket = context.wrap_socket(self.socket, server_hostname=self.host)
            self.ssl_socket.connect((self.host, self.port))
            self.ssl_socket.settimeout(self.wait)
            self.socket = self.ssl_socket
        else:
            self.socket.settimeout(self.wait)
            self.socket.connect((self.host, self.port))

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

    def send(self, data: bytes | str):
        try:
            self.socket.send(data)
        except EOFError:
            pass

    def recv(self, buffer_size: int) -> bytes:
        return self.socket.recv(buffer_size)

    def is_socket_open(self):
        try:
            self.socket.getpeername()
            return True
        except socket.error:
            return False

    def reset_socket(self):
        self.close()
        self._initialize_socket(self.host, self.port, self.wait, self.use_ssl, self.certfile)


class ConnectionHandler(IServerConnectionHandler):
    INACTIVITY_TIMEOUT = 5

    def __init__(self, client_socket: IClientSocket, command_processor: Callable,
                 handler_factory: IServerHandlerFactory, context: IClientContext):
        self.context = context
        self.handler_factory = handler_factory

        self.client_socket = client_socket
        self.command_processor = command_processor
        self.server_handler = None

        self.recent_activity = False
        self.last_check_time = time.time()

        self.first_connection = True

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
        # Send disconnect command to the server
        self.client_socket.close()

    def exchange_configurations(self):
        client_info = self.context.get_client_info_obj()

        try:
            # Listen for server to ask for client configuration
            data = self.client_socket.recv(1024)

            # Client should receive SCREEN_CONFIG_EXCHANGE_COMMAND
            if data.decode() == netConstants.SCREEN_CONFIG_EXCHANGE_COMMAND:
                # Send screen size to server
                screen_size = client_info.screen_size
                self.client_socket.send(f"{screen_size[0]}x{screen_size[1]}".encode())

                # Receive server screen size
                data = self.client_socket.recv(1024)
                server_screen_size = data.decode().split("x")

                client_info.server_screen_size = (int(server_screen_size[0]), int(server_screen_size[1]))
                self.context.set_client_info_obj(client_info)

                return True
        except (socket.error, ssl.SSLError) as e:
            self.logger(f"Error during configuration exchange: {e}", Logger.ERROR)
            return False

    def check_server_connection(self):
        current_time = time.time()
        if self.recent_activity or (
                current_time - self.last_check_time) > self.INACTIVITY_TIMEOUT:  # Check every 60 seconds if no activity
            if self.server_handler is not None and not self.server_handler.conn.is_socket_open():
                self.logger("Server connection closed.", Logger.WARNING)
                # self.server_handler.stop()
                self.server_handler = None

            self.recent_activity = False
            self.last_check_time = current_time

        if self.server_handler is None:
            if not self.first_connection:
                self.logger("Server connection lost.", Logger.WARNING)
            else:
                self.logger("Cannot establish server connection.", Logger.ERROR)
            return False

        return True


class SSLConnectionHandler(ConnectionHandler):
    def handle_connection(self):
        try:
            self.client_socket.connect()
            if not self.exchange_configurations():
                return False
            self.add_server_connection()
            self.logger("SSL connection established.")
            self.logger(
                f"Connected to server {self.client_socket.host}:{self.client_socket.port}, Secure: {self.client_socket.use_ssl}",
                Logger.INFO)
            self.first_connection = False
            return True
        except ServerNotFoundException as e:
            # Silently ignore this exception
            self.logger(e, Logger.DEBUG)
            return False
        except ConnectionRefusedError as e:
            self.client_socket.reset_socket()
            return False
        except Exception as e:
            self.logger(f"Error establishing SSL connection: {e}", Logger.ERROR)
            self.client_socket.reset_socket()
            return False

    def add_server_connection(self):
        # Adding server screen size to client info
        self.server_handler = self.handler_factory.create_handler(self.client_socket, self.command_processor)
        self.server_handler.start()


class NonSSLConnectionHandler(ConnectionHandler):
    def handle_connection(self):
        try:
            self.client_socket.connect()
            if not self.exchange_configurations():
                return False
            self.add_server_connection()
            self.logger("Non-SSL connection established.")
            return True
        except ServerNotFoundException as e:
            self.logger(e, Logger.DEBUG)
            return False
        except Exception as e:
            self.logger(f"Error establishing non-SSL connection: {e}", Logger.ERROR)
            self.client_socket.reset_socket()
            return False

    def add_server_connection(self):
        self.server_handler = self.handler_factory.create_handler(self.client_socket,
                                                                  self.command_processor)
        self.server_handler.start()


class ConnectionHandlerFactory(IConnectionHandlerFactory):

    @staticmethod
    def create_handler(ssl_enabled: bool, certfile: Optional[str] = None, keyfile: Optional[str] = None,
                       handler_socket: Optional[IClientSocket] = None,
                       context: Optional[IClientContext] = None,
                       command_processor: Callable[[str | tuple, str], None] = None,
                       handler_factory: IServerHandlerFactory = None) -> IServerConnectionHandler:
        if ssl_enabled:
            return SSLConnectionHandler(client_socket=handler_socket, command_processor=command_processor,
                                        handler_factory=handler_factory, context=context)
        else:
            return NonSSLConnectionHandler(client_socket=handler_socket, command_processor=command_processor,
                                           handler_factory=handler_factory, context=context)
