import socket
from time import sleep
from socket import timeout, error
from threading import Thread, Event
from typing import Optional, Callable, Any

from model.ClientObj import ClientsManager, ClientObj
from network.exceptions.ConnectionExceptions import ServerNotFoundException
from network.data.MessageExchange import MessageExchange
from network.protocol.message import MessageType
from ..stream import StreamType
from utils.logging import Logger

from .ClientSocket import ClientSocket

class ClientConnectionHandler:

    def __init__(self, msg_exchange: Optional['MessageExchange'] = None,
                 connected_callback: Optional[Callable[['ClientObj'], Any]] = None,
                 disconnected_callback: Optional[Callable[['ClientObj'], Any]] = None,
                 host: str = "0.0.0.0", port: int = 5001, wait: int = 5,
                 heartbeat_interval: int = 10, max_errors: int = 10,
                 clients: Optional[ClientsManager] = None,
                 open_streams: list[int] = None,
                 certfile: str = None):
        """
        A client-side connection handler that manages connections to a server,
        including handshake, stream management, and heartbeat.


        Args:
            msg_exchange (MessageExchange): Message exchange handler for communication.
            connected_callback (Callable): Callback function when connected to server.
            disconnected_callback (Callable): Callback function when disconnected from server.
            host (str): Server host address.
            port (int): Server port.
            wait (int): Wait time between connection attempts.
            heartbeat_interval (int): Interval for heartbeat messages.
            max_errors (int): Maximum allowed consecutive errors before stopping.
            clients (ClientsManager): Manager for client objects.
            open_streams (list[int]): List of stream types to open upon connection.
            certfile (str): Path to SSL certificate file, if using SSL.
        """

        self.msg_exchange = msg_exchange if msg_exchange is not None else MessageExchange()

        self.certfile = certfile
        self.clients = clients if clients is not None else ClientsManager(client_mode=True)

        self.connected_callback = connected_callback
        self.disconnected_callback = disconnected_callback

        self.host = host
        self.port = port
        self.wait = wait
        self.max_errors = max_errors

        self.socket_client: Optional[ClientSocket] = None
        self._initialized = False
        self._running = False
        self._core_thread = None
        self._core_event = Event() # Heartbeat thread event

        # Heartbeat parameters
        self.heartbeat_interval = heartbeat_interval  # seconds
        self._heartbeat_thread = None
        self._heartbeat_event = Event()

        self._connected = False
        self.open_streams = open_streams if open_streams is not None \
            else [StreamType.MOUSE, StreamType.KEYBOARD, StreamType.CLIPBOARD] # In addition to the command one

        self.logger = Logger.get_instance()

    def initialize(self):
        if not self._initialized:
            self.socket_client = ClientSocket(host=self.host, port=self.port,
                                              wait=self.wait, use_ssl=bool(self.certfile),
                                              certfile=self.certfile if self.certfile else "")
            self._initialized = True

            # Get current client obj from manager, if not present, create new
            client_obj = self.clients.get_client(ip_address=self.host)
            if not client_obj:
                client_obj = ClientObj(ip_address=self.host, ssl=self.socket_client.use_ssl)
                self.clients.add_client(client_obj)

            # Assign socket to client object
            client_obj.conn_socket = self.socket_client
            client_obj.ssl = True if self.certfile else False

            # Update client in manager
            self.clients.update_client(client_obj)

    def start(self) -> bool:
        try:
            if not self._initialized:
                self.initialize()

            if not self._running:
                self._running = True

                # Start core thread
                self._core_thread = Thread(target=self._core, daemon=True)
                self._core_thread.start()

                self._core_event.wait(timeout=1)
                if not self._core_event.is_set():
                    self.logger.log("Failed to start ClientConnectionHandler core loop.", Logger.ERROR)
                    self._running = False
                    return False
                self._core_event.clear()

                # Start heartbeat thread
                # self._heartbeat_thread = Thread(target=self._heartbeat_loop, daemon=True)
                # self._heartbeat_thread.start()
                # if not self._heartbeat_event.is_set():
                #     self.logger.log("Failed to start ServerConnectionHandler heartbeat loop.", Logger.ERROR)
                #     self._running = False
                #     return False
                # self._heartbeat_event.clear()

                self.logger.log("ClientConnectionHandler started.", Logger.INFO)
                return True

            return False
        except Exception as e:
            self.logger.log(f"Failed to start ClientConnectionHandler: {e}", Logger.ERROR)
            self._running = False
            return False


    def stop(self) -> bool:
        try:
            if self._running:
                self._running = False

                # Stop core thread
                if self._core_thread and self._core_thread.is_alive():
                    self._core_thread.join(timeout=5)

                # # Stop heartbeat thread
                # if self._heartbeat_thread and self._heartbeat_thread.is_alive():
                #     self._heartbeat_thread.join(timeout=5)

                # Close socket
                if self.socket_client:
                    self.socket_client.close()

                self.logger.log("ClientConnectionHandler stopped.", Logger.INFO)
        except Exception as e:
            self.logger.log(f"Error stopping ClientConnectionHandler: {e}", Logger.ERROR)
            return False

        return True

    # TODO: Better error handling
    def _core(self):
        self._core_event.set()
        error_count = 0
        while self._running:
            try:
                if not self.socket_client.is_socket_open() and not self._connected:
                    cmd_stream = self.socket_client.connect()
                    self.logger.log("Client connected, performing handshake...", Logger.INFO)

                    if not self._handshake(cmd_stream):
                        self.logger.log("Handshake failed.", Logger.ERROR)
                        sleep(self.wait)
                        continue

                    self._connected = True
                    error_count = 0
                else: # Heartbeat / connection check
                    if not self.socket_client.is_socket_open():
                        raise ConnectionResetError

                    # Connection is alive, reset error count
                    error_count = 0
                    sleep(self.wait) # Wait before next check
            except ServerNotFoundException as e:
                self.logger.log(f"Server not found: {e}", Logger.ERROR)
                sleep(self.wait)
            except ConnectionResetError:
                self.logger.log(f"Client disconnected from server.", Logger.WARNING)
                self._connected = False
                self.socket_client.close()
                client_obj = self.clients.get_client(ip_address=self.host)
                if client_obj:
                    client_obj.is_connected = False
                    self.clients.update_client(client_obj)

                    if self.disconnected_callback:
                        self.disconnected_callback(client_obj)
            except (timeout, error) as e:
                self.logger.log(f"Connection error - {e}", Logger.ERROR)

                client_obj = self.clients.get_client(ip_address=self.host)
                if client_obj:
                    client_obj.is_connected = False
                    self.clients.update_client(client_obj)

                error_count += 1
                if error_count >= self.max_errors:
                    self.logger.log("Max socket errors reached, closing connection.", Logger.ERROR)
                    self._running = False
                    self.socket_client.close()
                    # error_count = 0
                sleep(self.wait)
            except Exception as e:
                import traceback
                self.logger.log(f"Unexpected error in core loop: {e}", Logger.ERROR)
                self.logger.log(traceback.format_exc(), Logger.ERROR)
                client_obj = self.clients.get_client(ip_address=self.host)
                if client_obj:
                    client_obj.is_connected = False
                    self.clients.update_client(client_obj)
                self._running = False

    def _handshake(self, socket_stream: socket.socket) -> bool:
        self.msg_exchange.set_transport(socket_stream.send, socket_stream.recv)

        receive_handshake = False
        handshake_req = None
        attempts = 0
        max_attempts = 3

        while not receive_handshake:
            handshake_req = self.msg_exchange.receive_message(instant=True)
            if not handshake_req or handshake_req.message_type != MessageType.EXCHANGE:
                self.logger.log("Invalid handshake.", Logger.ERROR)
                attempts += 1
                if attempts >= max_attempts:
                    raise Exception("Max handshake attempts reached")
                sleep(1)
            else:
                receive_handshake = True

        if handshake_req.source != "server":
            self.logger.log("Handshake source is not server.", Logger.ERROR)
            return False

        self.logger.log(f"Received handshake from server.", Logger.DEBUG)

        # Get client infos from handshake target
        client_obj = self.clients.get_client(ip_address=self.host)
        if not client_obj:
            raise Exception("Critical internal error: Client object not found during handshake.")

        client_obj.screen_position = handshake_req.payload.get("screen_position")

        requested_streams = self.open_streams

        # Send handshake response
        self.msg_exchange.send_handshake_message(
            ack=True,
            source=client_obj.screen_position,
            target="server",
            streams=requested_streams,
            screen_position=client_obj.screen_position,
            screen_resolution=client_obj.screen_resolution,
            ssl=client_obj.ssl,
        )

        # Small delay to ensure server processes handshake
        sleep(0.2)
        retry_attempts = 0
        max_retries = 3
        for stream in requested_streams:
            try:
                self.socket_client.connect(stream_type=stream)
                self.logger.log(f"Stream {stream} connected.", Logger.DEBUG)
            except Exception as e:
                self.logger.log(f"Failed to connect stream {stream}: {e}", Logger.ERROR)
                retry_attempts += 1
                if retry_attempts >= max_retries:
                    self.logger.log("Max stream connection retries reached during handshake.", Logger.ERROR)
                    # Close all opened streams
                    self.socket_client.close()
                return False

        # Update client status
        client_obj.is_connected = True
        client_obj.conn_socket = self.socket_client # Re-assign socket just in case

        self.clients.update_client(client_obj)

        if self.connected_callback:
            self.connected_callback(client_obj)

        return True



