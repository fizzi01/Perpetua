import copy
import socket
import time
import ssl
from typing import Optional

from zeroconf import Zeroconf, ServiceStateChange, ServiceBrowser

from utils.logging import Logger

from config import ApplicationConfig
from .GeneralSocket import BaseSocket
from ..exceptions.ConnectionExceptions import ServerNotFoundException
from ..stream.StreamObj import StreamType


class ClientSocket(BaseSocket):
    _instance = None

    def __init__(self, host: str = "127.0.0.1", port: int = 5001, wait: int = 5, use_ssl: bool = False,
                           certfile: str = ""):
        super().__init__(address=(host, port))

        self.host = host
        self.port = port
        self.wait = wait
        self.use_ssl = use_ssl
        self.certfile = certfile

        self.use_discovery = False

        self.log = Logger.get_instance().log


    def __new__(cls, host: str, port: int, wait: int, use_ssl: bool, certfile: str):
        if cls._instance is None or not cls._instance.is_socket_open():
            cls._instance = super(ClientSocket, cls).__new__(cls)
            cls._instance._initialize_socket(host, port, wait, use_ssl, certfile)
        return cls._instance

    def _initialize_socket(self, host: str = "127.0.0.1", port: int = 5001, wait: int = 5, use_ssl: bool = False,
                           certfile: str = ""):
        self.host = host
        self.port = port
        self.wait = wait
        self.use_ssl = use_ssl
        self.certfile = certfile

        self.use_discovery = True if len(self.host) == 0 else False

    def _discover_server(self):
        service_found = False

        def on_service_state_change(zerocfg, service_type, name, state_change):
            nonlocal service_found
            if state_change == ServiceStateChange.Added and not service_found:
                info = zerocfg.get_service_info(service_type, name)
                if info and info.properties:
                    try:
                        properties = {key.decode(): value.decode() for key, value in info.properties.items()}
                        if properties.get("app_name") == ApplicationConfig.service_name and info.port == self.port:
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

    def connect(self, stream_type: Optional[int] = None, addr: str = "") -> socket.socket:
        if self.use_discovery and (not self.host or self.host == ""):
            self._discover_server()
            # Update the address after discovery
            self._address = (self.host, self.port)

        curr_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        curr_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        if self.use_ssl:
            context = ssl.create_default_context()
            context.load_verify_locations(cafile=self.certfile)
            ssl_context = context.wrap_socket(curr_socket, server_hostname=self.host)
            ssl_context.connect((self.host, self.port))
            ssl_context.settimeout(self.wait)
            curr_socket = ssl_context
        else:
            curr_socket.connect((self.host, self.port))
            curr_socket.settimeout(self.wait)

        # Add the connected stream (hard copy of the socket)
        if stream_type:
            self.put_stream(stream_type, curr_socket)
        else:
            self.put_stream(StreamType.COMMAND, curr_socket)

        return curr_socket



