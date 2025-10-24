"""
Provides logic to handle server-side socket connections.
It handles SSL and NON-SSL connections, heartbeats, and client management.
It handles information exchange between server and clients. (handshake)
"""
from time import sleep
import ssl
from socket import timeout, error
from threading import Thread, Event
from typing import Optional

from model.ClientObj import ClientsManager, ClientObj
from network.data.MessageExchange import MessageExchange
from network.protocol.message import MessageType
from utils.logging.logger import Logger

from .ServerSocket import ServerSocket, BaseSocket

class ServerConnectionHandler:
    """
    Manages server-side socket connections to multiple clients.
    It provides methods to start, stop, and monitor clients connections.
    It provides methods to handle information handshake between server and clients. (first connection)

    It creates ClientObj instances to represent connected clients and stores them in a client manager.
    It handles SSL and NON-SSL connections, heartbeats, and client management.
    It create a socket server to listen for incoming client connections.
    """

    def __init__(self, msg_exchange: Optional['MessageExchange'] = None,
                 connected_callback: Optional[callable] = None,
                 disconnected_callback: Optional[callable] = None,
                 host: str = "0.0.0.0", port: int = 5001, wait: int = 5,
                 heartbeat_interval: int = 10, max_errors: int = 10,
                 whitelist: Optional[ClientsManager] = None,
                 certfile: str = None, keyfile: str = None):
        self.msg_exchange = msg_exchange if msg_exchange is not None else MessageExchange()

        self.certfile = certfile
        self.keyfile = keyfile
        self.clients = whitelist if whitelist is not None else ClientsManager()

        self.connected_callback = connected_callback
        self.disconnected_callback = disconnected_callback

        self.host = host
        self.port = port
        self.wait = wait
        self.max_errors = max_errors

        self.socket_server: Optional[ServerSocket] = None
        self._initialized = False
        self._running = False
        self._core_thread = None
        self._core_event = Event() # Heartbeat thread event

        # Heartbeat parameters
        self.heartbeat_interval = heartbeat_interval  # seconds
        self._heartbeat_thread = None
        self._heartbeat_event = Event()

        self.logger = Logger.get_instance()

    def initialize(self):
        if not self._initialized:
            self.socket_server = ServerSocket(self.host, self.port, self.wait)
            self._initialized = True

    def start(self) -> bool:
        try:
            self._running = True
            self._core_thread = Thread(target=self._core, daemon=True)
            self._core_thread.start()

            # Check if the thread started properly
            self._core_event.wait(timeout=1)
            if not self._core_event.is_set():
                self.logger.log("Failed to start ServerConnectionHandler core loop.", Logger.ERROR)
                self._running = False
                return False
            self._core_event.clear()

            # Start heartbeat thread
            self._heartbeat_thread = Thread(target=self._heartbeat_loop, daemon=True)
            self._heartbeat_thread.start()
            if not self._heartbeat_event.is_set():
                self.logger.log("Failed to start ServerConnectionHandler heartbeat loop.", Logger.ERROR)
                self._running = False
                return False
            self._heartbeat_event.clear()
            return True
        except Exception as e:
            self.logger.log(f"Failed to start ServerConnectionHandler: {e}", Logger.ERROR)
            self._running = False
            return False

    def stop(self):
        self._running = False
        if self.socket_server:
            self.socket_server.close()
        try:
            if self._core_thread and self._core_thread.is_alive():
                self._core_thread.join(timeout=5)
            if self._heartbeat_thread and self._heartbeat_thread.is_alive():
                self._heartbeat_thread.join(timeout=5)
        except Exception as e:
            self.logger.log(f"Error stopping ServerConnectionHandler threads: {e}", Logger.ERROR)

        self.logger.log("ServerConnectionHandler stopped.", Logger.DEBUG)

    def _core(self):
        """
        Core loop to accept and handle client connections.
        """
        self._core_event.set()
        error_count = 0
        while self._running:
            try:
                client_socket, addr = self.socket_server.accept()
                self.logger.log(f"Accepted connection from {addr}", Logger.INFO)

                client_obj = self.clients.get_client(ip_address=addr[0])
                if not client_obj:
                    self.logger.log(f"Client {addr[0]} not in whitelist. Closing connection.", Logger.WARNING)
                    client_socket.close()
                    continue

                # Perform handshake and update client info
                if self._handshake(client_socket, client_obj):

                    # Check if SSL is required for this client
                    if client_obj.ssl:
                        client_socket = self._ssl_wrap(client_socket)
                        self.logger.log(f"SSL connection established with client {addr[0]}.", Logger.INFO)
                    else:
                        self.logger.log(f"Non-SSL connection established with client {addr[0]}.", Logger.INFO)

                    client_obj.conn_socket = BaseSocket(client_socket, addr)
                    client_obj.is_connected = True
                    self.clients.update_client(client_obj)
                    if self.connected_callback:
                        self.connected_callback(client_obj)
                    self.logger.log(f"Client {addr[0]} connected and handshake completed.", Logger.INFO)
                else:
                    self.logger.log(f"Handshake failed for client {addr[0]}. Closing connection.", Logger.WARNING)
                    client_socket.close()
                    client_obj.conn_socket = None
                    client_obj.is_connected = False
                    self.clients.update_client(client_obj)

                error_count = 0  # Reset error count on successful iteration
            except timeout:
                # Periodic check of client connections
                if self._running:
                    continue
                else:
                    break
            except Exception as e:
                import traceback
                if self._running:
                    self.logger.log(f"Error in connection service core loop: {e}", Logger.ERROR)
                    self.logger.log(traceback.format_exc(), Logger.ERROR)
                    error_count += 1
                    if error_count >= self.max_errors:
                        self.logger.log("Too many errors in core loop. Stopping the server connection handler.", Logger.ERROR)
                        self._running = False
                        break
                    continue
                else:
                    break


    def _handshake(self, client_socket, client: ClientObj):
        """
        Perform handshake with the connected client to exchange information.
        Returns a dictionary with client information.
        """
        # Server sends handshake request
        self.msg_exchange.set_transport(client_socket.send, client_socket.recv)
        self.msg_exchange.send_handshake_message(ack=False,source="server", target=client.screen_position)

        # Server waits for client response
        response = self.msg_exchange.receive_message(instant=True)
        if response:
            # Check for acknowledgment and extract client info
            if response.message_type == MessageType.EXCHANGE and response.payload.get("ack", False):
                client.screen_resolution = response.payload.get("screen_resolution", None)
                client.additional_params = response.payload.get("additional_params", {})
                client.ssl = response.payload.get("ssl", False)
                self.logger.log(f"Handshake successful with client {client.ip_address}", Logger.INFO)
                return True
            else:
                self.logger.log(f"Invalid handshake response from client {client.ip_address}", Logger.WARNING)
        else:
            self.logger.log(f"No response during handshake from client {client.ip_address}", Logger.WARNING)

        return False

    def _heartbeat_loop(self):
        """
        Periodically checks connected clients' heartbeats.
        """
        # For every connected client, use is_socket_open method of the ServerSocket to check if the socket is still open.
        self._heartbeat_event.set()
        while self._running:
            sleep(self.heartbeat_interval)
            for client in self.clients.clients:
                if client.is_connected and client.conn_socket:
                    try:
                        if not client.conn_socket.is_socket_open():
                            raise ConnectionResetError
                        # Update active time
                        client.connection_time += self.heartbeat_interval
                    except (error, ConnectionResetError):
                        self.logger.log(f"Client {client.ip_address} disconnected (heartbeat failed).", Logger.WARNING)
                        client.is_connected = False
                        client.conn_socket.close()
                        client.conn_socket = None
                        self.clients.update_client(client)

    def _ssl_wrap(self, client_socket):
        """
        Wrap the client socket with SSL.
        """
        context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        context.load_cert_chain(certfile=self.certfile, keyfile=self.keyfile)
        ssl_socket = context.wrap_socket(client_socket, server_side=True)
        return ssl_socket


