import asyncio
import socket
from typing import Optional, Callable
import time
import uuid

from aioquic.asyncio import serve
from aioquic.quic.configuration import QuicConfiguration
from zeroconf import ServiceInfo, Zeroconf, ServiceStateChange, ServiceBrowser

from utils.logging.logger import Logger
from utils.net import NetUtils

from config import ApplicationConfig
from network.connection.GeneralSocket import QuicSocket, QuicProtocol


class ServerSocket:
    """
    ServerSocket class for creating and managing a singleton server socket instance.

    This class provides functionality for creating a TCP server socket, registering
    it as a local mDNS service, and managing its lifecycle. It ensures that only one
    instance of the server socket exists across the application and handles
    initialization, binding, listening, and closing operations. Additionally, it
    resolves port conflicts when registering the service and maintains compatibility
    with local mDNS services.

    Attributes:
        _instance: The singleton instance of the ServerSocket class.
        socket: The TCP server socket instance.
        host: The hostname or IP address of the server socket.
        port: The port number on which the server socket listens.
        log: A logging function for server events.
        zeroconf: An instance of the Zeroconf class to handle mDNS operations.

    Methods:
        get_host():
            Returns the host of the server socket.
        get_port():
            Returns the port of the server socket.
        bind_and_listen():
            Binds the server socket to its host and port and starts listening for
            incoming connections.
        accept():
            Accepts an incoming connection to the server socket.
        pause():
            Detaches the server socket, pausing its listening operations.
        close():
            Closes the server socket and unregisters any associated mDNS services.
        is_socket_open():
            Checks whether the server socket is currently open.
    """
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
                        if properties.get("app_name") == ApplicationConfig.service_name and info.port == port:
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
            f"{ApplicationConfig.service_name}-{uuid.uuid4().hex[:8]}._http._tcp.local.",
            addresses=[socket.inet_aton(self.host)],
            port=self.port,
            properties={"app_name": ApplicationConfig.service_name},
            server=f"{ApplicationConfig.service_name}.local.",
        )
        self.zeroconf.register_service(service_info)
        self.log(f"[mDNS] Server service {service_info.name} registered", Logger.DEBUG)

    def _unregister_mdns_service(self):
        self.zeroconf.unregister_all_services()
        self.zeroconf.close()
        self.log(f"[mDNS] Server service {ApplicationConfig.service_name} unregistered.", Logger.DEBUG)



class QuicServerSocket(QuicSocket):
    """Socket server QUIC per accettare connessioni client"""

    def __init__(self,
                 host: str = "0.0.0.0",
                 port: int = 5001,
                 certfile: Optional[str] = None,
                 keyfile: Optional[str] = None):
        super().__init__(host, port)
        self.certfile = certfile
        self.keyfile = keyfile
        self._server_task: Optional[asyncio.Task] = None
        self.zeroconf = Zeroconf()
        self._on_client_connected: Optional[Callable] = None
        self._on_stream_data_received: Optional[Callable] = None

    def set_callbacks(self,
                      on_client_connected: Optional[Callable] = None,
                      on_stream_data_received: Optional[Callable] = None):
        """Imposta callback per eventi del server"""
        self._on_client_connected = on_client_connected
        self._on_stream_data_received = on_stream_data_received

    async def start(self):
        """Avvia il server QUIC"""
        await self.bind_and_listen()

    async def bind_and_listen(self):
        """Bind e listen del server QUIC"""
        configuration = QuicConfiguration(
            is_client=False,
            alpn_protocols=["hq-interop"],
        )

        # Carica certificati SSL
        if self.certfile and self.keyfile:
            configuration.load_cert_chain(self.certfile, self.keyfile)
        else:
            self.logger.log(
                "[QUIC] Warning: No SSL certificates provided",
                Logger.WARNING
            )

        # Registra servizio mDNS
        self._resolve_port_conflict()
        self._register_mdns_service()

        # Avvia server
        self._server_task = asyncio.create_task(
            serve(
                self.host,
                self.port,
                configuration=configuration,
                create_protocol=self._create_protocol,
            )
        )

        self.logger.log(
            f"[QUIC] Server listening on {self.host}:{self.port}",
            Logger.INFO
        )

    def _create_protocol(self, *args, **kwargs) -> QuicProtocol:
        """Factory per creare il protocollo QUIC"""
        protocol = QuicProtocol(*args, **kwargs)
        protocol.on_stream_data = self._handle_stream_data
        protocol.on_connection_ready = self._on_connection_ready
        protocol.on_connection_lost = self._on_connection_lost

        self.protocol = protocol
        return protocol

    async def _on_connection_ready(self, protocol: QuicProtocol):
        """Callback quando un client si connette"""
        self.logger.log("[QUIC] Client connected", Logger.INFO)

        if self._on_client_connected:
            await self._on_client_connected(protocol)

    async def _handle_stream_data(self, stream_id: int, data: bytes, end_stream: bool):
        """Gestisce dati ricevuti su uno stream"""
        if self._on_stream_data_received:
            await self._on_stream_data_received(stream_id, data, end_stream)

    async def _on_connection_lost(self):
        """Callback quando la connessione viene persa"""
        self.logger.log("[QUIC] Connection lost", Logger.WARNING)
        self.protocol = None

    def _register_mdns_service(self):
        """Registra il servizio mDNS"""
        from utils.net import NetUtils

        self.host = NetUtils.get_local_ip()
        if not self.host:
            raise Exception("No network connection available")

        service_info = ServiceInfo(
            "_quic._udp.local.",
            f"{ApplicationConfig.service_name}-{uuid.uuid4().hex[:8]}._quic._udp.local.",
            addresses=[socket.inet_aton(self.host)],
            port=self.port,
            properties={"app_name": ApplicationConfig.service_name},
            server=f"{ApplicationConfig.service_name}.local.",
        )

        self.zeroconf.register_service(service_info)
        self.logger.log(
            f"[mDNS] QUIC service {service_info.name} registered",
            Logger.DEBUG
        )

    def _resolve_port_conflict(self):
        """Controlla se esiste un conflitto sulla porta con altri servizi mDNS."""
        while self._is_port_in_use_by_mdns(self.port):
            self.logger.log(f"[mDNS] Port {self.port} is already in use. Trying next port...", Logger.WARNING)
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
                        if properties.get("app_name") == ApplicationConfig.service_name and info.port == port:
                            conflict_found = True
                    except AttributeError:
                        pass

        browser = ServiceBrowser(self.zeroconf, "_http._tcp.local.", handlers=[on_service_state_change])
        time.sleep(1)  # Aspetta che i servizi vengano scoperti
        browser.cancel()
        return conflict_found

    async def close(self):
        """Chiude il server QUIC"""
        if self._server_task:
            self._server_task.cancel()
            try:
                await self._server_task
            except asyncio.CancelledError:
                pass

        self._unregister_mdns_service()
        await super().close()
        self.logger.log("[QUIC] Server closed", Logger.INFO)

    def _unregister_mdns_service(self):
        """Deregistra il servizio mDNS"""
        self.zeroconf.unregister_all_services()
        self.zeroconf.close()
        self.logger.log(
            f"[mDNS] QUIC service unregistered",
            Logger.DEBUG
        )
