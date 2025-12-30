"""
Provides logic to handle server-side socket connections using asyncio.
Handles SSL and NON-SSL connections, heartbeats, and client management.
"""

import asyncio
from asyncio.futures import Future

import ssl
from typing import Optional, Callable, Any

from model.client import ClientsManager, ClientObj
from network.data.exchange import MessageExchange, MessageExchangeConfig
from network.protocol.message import MessageType
from network.stream import StreamType
from utils.logging import Logger, get_logger

from . import ClientConnection, StreamWrapper
from .handler import CallbackError, BaseConnectionHandler


class ConnectionHandler(BaseConnectionHandler):
    """
    Manages server-side socket connections using asyncio.

    Fully optimized for asyncio with non-blocking I/O, efficient handshakes,
    and automatic heartbeat monitoring.

    Attributes:
        connected_callback (callable): Callback for client connection.
        disconnected_callback (callable): Callback for client disconnection.
        reconnected_callback (callable): Callback for client streams reconnection.
        host (str): Server host address.
        port (int): Server port.
        heartbeat_interval (int): Interval for heartbeat checks.
        allowlist (ClientsManager): Shared Manager for clients.
        certfile (str): SSL certificate file path.
        keyfile (str): SSL key file path.
    """

    HANDSHAKE_DELAY = 0.2  # sec
    HANDSHAKE_MSG_TIMEOUT = 5.0  # sec
    CONNECTION_ATTEMPT_TIMEOUT = 10  # sec
    MAX_HEARTBEAT_MISSES = 0

    def __init__(
        self,
        connected_callback: Optional[Callable[["ClientObj", list[int]], Any]] = None,
        disconnected_callback: Optional[Callable[["ClientObj", list[int]], Any]] = None,
        reconnected_callback: Optional[Callable[["ClientObj", list[int]], Any]] = None,
        host: str = "0.0.0.0",
        port: int = 5001,
        heartbeat_interval: int = 2,
        allowlist: Optional[ClientsManager] = None,
        certfile: Optional[str] = None,
        keyfile: Optional[str] = None,
    ):
        self.certfile = certfile
        self.keyfile = keyfile
        self.clients = allowlist if allowlist is not None else ClientsManager()

        self.connected_callback = connected_callback
        self.disconnected_callback = disconnected_callback
        self.reconnected_callback = reconnected_callback

        self.host = host
        self.port = port
        self.heartbeat_interval = heartbeat_interval

        self.server = None
        self._running = False
        self._heartbeat_task = None
        self._server_task = None
        self._pending_streams: dict[
            str, dict[int, Future]
        ] = {}  # {ip_address: {stream_type: Future}}

        self._logger = get_logger(self.__class__.__name__)

    async def start(self) -> bool:
        """Avvia il server asyncio con heartbeat monitoring"""
        try:
            self._running = True

            # Crea il server asyncio
            self.server = await asyncio.start_server(
                self._handle_client,
                self.host,
                self.port,
                # ssl=self._get_ssl_context() Choose it based on client request in handshake
            )
            # Serve forever in background
            self._server_task = asyncio.create_task(self._serve_forever())

            self._logger.log(f"Started on {self.host}:{self.port}", Logger.INFO)

            # Avvia heartbeat task
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

            return True
        except Exception as e:
            self._logger.exception(f"Failed to start async server -> {e}")
            self._running = False
            return False

    async def stop(self):
        """Ferma il server e chiude tutte le connessioni in modo pulito"""
        if not self._running:
            return True

        self._running = False

        # Cancella heartbeat task
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass

        # # Cancella tutti i task dei client
        # for task in self._client_tasks.values():
        #     if not task.done():
        #         task.cancel()
        #
        # if self._client_tasks:
        #     await asyncio.gather(*self._client_tasks.values(), return_exceptions=True)
        #     self._client_tasks.clear()

        # Disconnetti tutti i client
        for client in self.clients.get_clients():
            await self.force_disconnect_client(client)

        # Cancella server task
        if self._server_task and not self._server_task.done():
            self._server_task.cancel()
            try:
                if self.server:
                    self.server.close()
                    # Not working properly in 3.12 (but yes in 3.11)
                    try:
                        await asyncio.wait_for(self.server.wait_closed(), timeout=5.0)
                    except asyncio.TimeoutError:
                        self._logger.log(
                            "Timeout while waiting for server to close.", Logger.WARNING
                        )
                self._server_task.cancel()
                # await self._server_task
            except asyncio.TimeoutError:
                self._logger.log("Timeout while closing server.", Logger.WARNING)
            except asyncio.CancelledError:
                pass

        self._logger.log("Stopped.", Logger.INFO)
        return True

    async def _serve_forever(self):
        """Serve forever loop"""
        if self.server is None:
            self._logger.log("Server not started.", Logger.ERROR)
            return

        try:
            async with self.server:
                await self.server.serve_forever()
        except asyncio.CancelledError:
            self._logger.log("Server loop cancelled.", Logger.INFO)
        except Exception:
            self._logger.log("Server encountered an error.", Logger.CRITICAL)
            import traceback

            self._logger.log(traceback.format_exc(), Logger.ERROR)

    async def force_disconnect_client(self, client: ClientObj):
        """
        Forced disconnection of a specific client.
        """
        if client.is_connected and client.get_connection() is not None:
            try:
                await client.get_connection().wait_closed()  # type: ignore
                await asyncio.sleep(0.1)  # Small delay to ensure closure
                self._logger.log(
                    f"Force disconnected client {client.get_net_id()}", Logger.INFO
                )
            except Exception as e:
                self._logger.log(
                    f"Error force disconnecting client {client.get_net_id()} -> {e}",
                    Logger.ERROR,
                )
            client.is_connected = False
            client.set_connection(None)
            self.clients.update_client(client)

            try:
                await self._invoke_callback(
                    callback=self.disconnected_callback, client=client, streams=[]
                )
            except CallbackError as e:
                self._logger.log(f"Error in disconnected callback -> {e}", Logger.ERROR)

    async def _check_pending_streams(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, address: str
    ) -> bool:
        """
        Checks for pending streams associated with a specific address and handles their
        completion if applicable.

        This method verifies if there are any pending streams waiting to be resolved
        for a given address. When a match is found, it fulfills the associated
        future with the current reader and writer streams. The method also logs the
        status of the completed future and cleans up its associated resources.

        Args:
            reader (asyncio.StreamReader): The stream reader associated with the
                new connection.
            writer (asyncio.StreamWriter): The stream writer associated with the
                new connection.
            address (str): The address of the client initiating the connection.

        Returns:
            bool: True if a pending stream was resolved, otherwise False.
        """
        try:
            if (
                address in self._pending_streams
                and self._pending_streams[address] is not None
            ):
                # There are pending streams for this address
                # Get the first pending stream (there should be only one per type)
                pending = self._pending_streams[address]
                if pending:
                    stream_type = next(iter(pending.keys()))
                    future = pending[stream_type]

                    if not future.done():
                        future.set_result((reader, writer))
                        self._logger.log(
                            f"Stream {stream_type} accepted from {address}",
                            Logger.DEBUG,
                        )
                    else:
                        self._logger.log(
                            f"Future already done for stream {stream_type} from {address}",
                            Logger.WARNING,
                        )
                        writer.close()
                        await writer.wait_closed()

                    del pending[stream_type]
                    return True
        except Exception as e:
            self._logger.log(
                f"Error checking pending streams for {address} -> {e}", Logger.ERROR
            )

        return False

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ):
        """Gestisce una nuova connessione client (handshake o stream aggiuntivo)"""
        addr = writer.get_extra_info("peername")
        self._logger.log(f"Accepted connection from {addr}", Logger.DEBUG)

        try:
            client_obj = self.clients.get_client(ip_address=addr[0])
            if await self._check_pending_streams(
                reader=reader, writer=writer, address=addr[0]
            ):
                return  # Pending stream accepted let the handshake handler manage it

            # Altrimenti è una connessione di handshake
            if client_obj and client_obj.is_connected:
                self._logger.log(
                    f"Client {addr[0]} is already connected. Closing new connection.",
                    Logger.WARNING,
                )
                writer.close()
                await writer.wait_closed()
                return

            # TODO: Move client allowlist verification here and let user allow/block before handshake

            # Handshake
            if not await self._handshake(reader, writer, addr[0], client_obj):
                self._logger.log(
                    f"Handshake failed for client {addr[0]}. Closing connection.",
                    Logger.WARNING,
                )
                writer.close()
                await writer.wait_closed()

        except Exception as e:
            self._logger.log(f"Error handling client {addr} -> {e}", Logger.ERROR)
            import traceback

            self._logger.log(traceback.format_exc(), Logger.ERROR)

    @staticmethod
    def _check_client(client_obj: ClientObj, address: str) -> bool:
        """
        Check if client_obj is allowed to connect by matching IP address.
        """
        return client_obj.ip_address == address

    async def _handshake(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        client_addr,
        client: Optional[ClientObj],
    ) -> bool:
        """
        Esegue l'handshake con il client usando MessageExchange asyncio.

        Returns:
            True se handshake completato con successo, False altrimenti
        """
        try:
            # Dedicated MessageExchange for handshake
            config = MessageExchangeConfig(
                max_chunk_size=4096,
                auto_chunk=True,
                auto_dispatch=False,  # We want to control message handling manually
            )
            client_msg_exchange = MessageExchange(config, id=f"Handshake_{client_addr}")
            cur_stream = StreamWrapper(reader=reader, writer=writer)

            await client_msg_exchange.set_transport(
                cur_stream.get_writer_call(), cur_stream.get_reader_call()
            )

            # Invia handshake request
            self._logger.log(
                f"Sending handshake request to client {client_addr}", Logger.DEBUG
            )
            await client_msg_exchange.send_handshake_message(
                ack=False,
                source="server",
                # screen_position=client.screen_position,
                # target=client.screen_position
            )

            # Avvia ricezione per processare la risposta
            await client_msg_exchange.start()

            # Attendi risposta con timeout
            try:
                response = await asyncio.wait_for(
                    client_msg_exchange.get_received_message(),
                    timeout=self.HANDSHAKE_MSG_TIMEOUT,
                )
            except asyncio.TimeoutError:
                self._logger.log(
                    f"Handshake timeout for client {client_addr}", Logger.WARNING
                )
                await client_msg_exchange.stop()
                return False

            # Verifica risposta
            if (
                response
                and response.message_type == MessageType.EXCHANGE
                and response.payload.get("ack", False)
            ):
                # Precheck client
                tmp_host_name = response.source  # Client will provide its hostname

                if tmp_host_name is not None and client is None:
                    # Get client obj by hostname if not found by IP
                    client = self.clients.get_client(hostname=tmp_host_name)
                    if client:
                        client.ip_address = (
                            client_addr  # Update IP address based on connection
                        )
                elif client is None:
                    # Try again to get client by IP if hostname not provided
                    client = self.clients.get_client(ip_address=client_addr)

                if (
                    client is None
                ):  # Client not found - Client is None so not in allowlist
                    self._logger.log(
                        f"Client with IP {client_addr} not found in allowlist.",
                        Logger.WARNING,
                    )
                    await client_msg_exchange.send_handshake_message(
                        ack=False,
                        source="server",
                    )
                    await asyncio.sleep(self.HANDSHAKE_DELAY)
                    await client_msg_exchange.stop()
                    await cur_stream.close()
                    return False

                if not self._check_client(
                    client_obj=client, address=client_addr
                ):  # Client is not allowed
                    # Stop handshake if client not allowed
                    self._logger.log(
                        f"Client {client.get_net_id()} not allowed to connect.",
                        Logger.WARNING,
                    )
                    await client_msg_exchange.stop()
                    await cur_stream.close()
                    return False

                # Normal flow: update client info
                client.screen_resolution = response.payload.get(
                    "screen_resolution", None
                )
                client.additional_params = response.payload.get("additional_params", {})
                client.ssl = response.payload.get("ssl", False)
                requested_streams = response.payload.get("streams", [])

                self._logger.log(
                    f"Client {client.get_net_id()}",
                    Logger.DEBUG,
                    client=client.to_dict(),
                )

                # Crea AsyncClientConnection per gestire multiple streams asyncio
                conn = ClientConnection(client_addr)
                conn.add_stream(stream_type=StreamType.COMMAND, stream=cur_stream)
                client.set_connection(connection=conn)

                # Send position info back to client
                await client_msg_exchange.send_handshake_message(
                    ack=True,
                    source="server",
                    screen_position=client.screen_position,
                    target=client.screen_position,
                )

                # Accetta stream aggiuntivi richiesti dal client
                if requested_streams:
                    self._logger.log(
                        f"Client {client.get_net_id()} requested {len(requested_streams)} additional streams",
                        Logger.DEBUG,
                    )

                    if not await self._accept_additional_streams(
                        client, requested_streams
                    ):
                        self._logger.log(
                            f"Failed to accept additional streams for client {client.get_net_id()}",
                            Logger.WARNING,
                        )
                        await client_msg_exchange.stop()
                        return False

                # Cleanup temporaneo del msg_exchange (il client ne avrà uno proprio)
                await client_msg_exchange.stop()

                client.is_connected = True
                client.set_first_connection()
                self.clients.update_client(client)

                if self.connected_callback:
                    try:
                        c_conn = client.get_connection()
                        c_streams = []
                        if c_conn is not None:
                            c_streams = c_conn.get_available_stream_types()

                        await self._invoke_callback(
                            callback=self.connected_callback,
                            client=client,
                            streams=c_streams,
                        )
                    except Exception as e:
                        self._logger.log(
                            f"Error in connected callback -> {e}", Logger.ERROR
                        )

                self._logger.log(
                    f"Client {client.get_net_id()} connected and handshake completed.",
                    Logger.INFO,
                )
                return True
            elif client is not None:
                self._logger.log(
                    f"Invalid handshake response from client {client.get_net_id()}",
                    Logger.WARNING,
                )
                await client_msg_exchange.stop()
                return False
            else:
                self._logger.log(
                    f"Invalid handshake response from unknown client {client_addr}",
                    Logger.WARNING,
                )
                await client_msg_exchange.stop()
                return False

        except asyncio.CancelledError:
            self._logger.log(
                f"Handshake cancelled for client {client_addr}", Logger.WARNING
            )
            raise
        except Exception as e:
            self._logger.log(
                f"Handshake error with client {client_addr} -> {e}", Logger.ERROR
            )
            # import traceback
            # self._logger.log(traceback.format_exc(), Logger.ERROR)
            return False

    async def _accept_additional_streams(
        self, client: ClientObj, requested_streams: list[int]
    ) -> bool:
        """
        Accepts and establishes additional streams requested by a client. The method handles
        stream setup, validation, connection, SSL wrapping (if enabled), and stream lifecycle.

        Args:
            client (ClientObj): The client object requesting additional streams.
            requested_streams (list[int]): The list of stream types requested to be added.

        Returns:
            bool: True if all requested streams are successfully connected, otherwise False.

        Raises:
            ValueError: If the client's IP address is None or invalid.
            ConnectionError: If the client's connection is lost during the handshake.
        """
        if not requested_streams:
            return True

        if client.ip_address is None or not isinstance(client.ip_address, str):
            raise ValueError("Client IP address is None")

        # Prepara i future per gli stream in arrivo
        if client.ip_address not in self._pending_streams:
            self._pending_streams[client.ip_address] = {}

        for stream_type in requested_streams:
            if not isinstance(stream_type, int) or not StreamType.is_valid(stream_type):
                self._logger.log(
                    f"Invalid stream type requested: {stream_type}",
                    Logger.WARNING,
                )
                continue

            try:
                # Crea un future per questo stream
                stream_future = asyncio.Future()
                self._pending_streams[client.ip_address][stream_type] = stream_future

                # Attendi che il client si connetta per questo stream
                stream_reader, stream_writer = await asyncio.wait_for(
                    stream_future, timeout=self.CONNECTION_ATTEMPT_TIMEOUT
                )

                stream_addr = stream_writer.get_extra_info("peername")

                # Wrap SSL
                if client.ssl and self.certfile and self.keyfile:
                    await asyncio.wait_for(
                        stream_writer.start_tls(self._get_ssl_context()),
                        timeout=self.CONNECTION_ATTEMPT_TIMEOUT,
                    )
                    self._logger.log(
                        f"SSL stream connection for {stream_type} from {stream_addr}",
                        Logger.INFO,
                    )

                conn = client.get_connection()
                if conn is None:
                    raise ConnectionError("Client connection lost during handshake")

                conn.add_stream(
                    stream_type=stream_type,
                    reader=stream_reader,
                    writer=stream_writer,
                )
                client.set_connection(connection=conn)
                client.open_streams[stream_type] = stream_addr[1]

                self._logger.log(
                    f"Stream {stream_type} connected from {stream_addr}",
                    Logger.DEBUG,
                )

            except asyncio.TimeoutError:
                self._logger.log(
                    f"Timeout waiting for stream {stream_type} from client {client.get_net_id()}",
                    Logger.WARNING,
                )
                self._cleanup_pending_stream(client.ip_address, stream_type)
                return False

            except Exception as e:
                self._logger.log(
                    f"Error accepting stream {stream_type} -> {e}",
                    Logger.ERROR,
                )
                self._cleanup_pending_stream(client.ip_address, stream_type)
                return False

        # Pulisci i pending streams per questo client
        if client.ip_address in self._pending_streams:
            del self._pending_streams[client.ip_address]

        return True

    def _cleanup_pending_stream(self, ip_address: str, stream_type: int):
        """Pulisce un pending stream specifico."""
        if ip_address in self._pending_streams:
            fut = self._pending_streams[ip_address].pop(stream_type, None)
            if fut and not fut.done():
                fut.cancel()
            if not self._pending_streams[ip_address]:
                del self._pending_streams[ip_address]

    async def _handle_hartbeat_failure(
        self, client: ClientObj, err: Optional[Exception] = None
    ):
        """Gestisce il fallimento del heartbeat per un client"""
        self._logger.log(
            f"Client {client.get_net_id()} disconnected ({err}).", Logger.WARNING
        )
        client.is_connected = False

        try:
            conn = client.get_connection()
            if conn is not None:
                await conn.wait_closed()
        except Exception as e:
            self._logger.warning(
                f"Error while waiting for client {client.get_net_id()} connection to close -> {e}"
            )

        client.set_connection(None)
        self.clients.update_client(client)

        try:
            await self._invoke_callback(
                callback=self.disconnected_callback, client=client, streams=[]
            )
        except CallbackError as e:
            self._logger.log(f"Error in disconnected callback -> {e}", Logger.ERROR)

    async def _handle_streams_reconnection(
        self, client: ClientObj, closed_streams: list[int]
    ) -> bool:
        return await self._accept_additional_streams(client, closed_streams)

    async def _heartbeat_loop(self):
        """Loop di heartbeat per verificare le connessioni e aggiornare metriche"""
        try:
            # # Init MessageExchange config one time
            # config = MessageExchangeConfig(
            #     max_chunk_size=4096,
            #     auto_chunk=True,
            #     auto_dispatch=False,  # We want to control message handling manually
            # )
            heartbeat_trials = {}  # We store trials per client so we can implement a retry mechanism

            while self._running:
                await asyncio.sleep(self.heartbeat_interval)

                for client in self.clients.get_clients():
                    # Add heartbeat trials tracking
                    if client.get_net_id() not in heartbeat_trials:
                        heartbeat_trials[client.get_net_id()] = 0

                    if client.is_connected and client.get_connection() is not None:
                        try:
                            client_conn = client.get_connection()
                            if client_conn is None:
                                raise ConnectionResetError("No connection found")

                            if not await client_conn.is_open():
                                raise ConnectionResetError("Connection is closed")

                            cmd_stream = client_conn.get_stream(StreamType.COMMAND)
                            if cmd_stream is None:
                                raise ConnectionResetError("Command stream not found")

                            # Send heartbeat message
                            # client_msg_exchange = MessageExchange(config, id=f"Heartbeat_{client.get_net_id()}")
                            # await client_msg_exchange.set_transport(cmd_stream.get_writer_call(), None)
                            # await client_msg_exchange.send_custom_message(message_type="HEARTBEAT", payload={})

                            # Check eof on reader
                            command_reader = client_conn.get_reader(StreamType.COMMAND)
                            if command_reader is None or command_reader.is_closed():
                                raise ConnectionResetError(
                                    "Command stream reader is closed"
                                )

                            # Check others streams to handle reconnection
                            closed_streams: list[int] = []
                            for stream_type in client_conn.get_available_stream_types():
                                # Skip command stream (already checked)
                                if stream_type == StreamType.COMMAND:
                                    continue

                                stream_reader = client_conn.get_reader(stream_type)
                                stream_writer = client_conn.get_writer(stream_type)
                                if (
                                    stream_reader is None or stream_reader.is_closed()
                                ) or (
                                    stream_writer is None
                                    or await stream_writer.is_closed()
                                ):
                                    # Force closure of the stream writer if it exists
                                    if stream_writer:
                                        await stream_writer.close()
                                    closed_streams.append(stream_type)

                            if len(closed_streams) > 0:
                                self._logger.warning(
                                    "Detected closed streams, attempting reconnection...",
                                    client=client.get_net_id(),
                                    closed_streams=closed_streams,
                                )

                                if not await self._handle_streams_reconnection(
                                    client, closed_streams
                                ):
                                    raise ConnectionResetError(
                                        f"Streams {closed_streams} are closed and reconnection failed"
                                    )
                                else:
                                    self._logger.info(
                                        "Reconnected closed streams successfully.",
                                        client=client.get_net_id(),
                                        reconnected_streams=closed_streams,
                                    )
                                    try:
                                        await self._invoke_callback(
                                            callback=self.reconnected_callback,
                                            client=client,
                                            streams=closed_streams,
                                        )
                                    except CallbackError as e:
                                        self._logger.log(
                                            f"Error in reconnected callback -> {e}",
                                            Logger.ERROR,
                                        )

                            # Update active time
                            client.set_last_connection()
                        except (ConnectionResetError, OSError) as e:
                            if (
                                heartbeat_trials[client.get_net_id()]
                                < self.MAX_HEARTBEAT_MISSES
                            ):
                                heartbeat_trials[client.get_net_id()] += 1
                                self._logger.log(
                                    f"Heartbeat missed for client {client.get_net_id()} (trial {heartbeat_trials[client.get_net_id()]}/{self.MAX_HEARTBEAT_MISSES})",
                                    Logger.WARNING,
                                )
                            else:
                                await self._handle_hartbeat_failure(client, err=e)
                                heartbeat_trials[client.get_net_id()] = 0
                        except Exception as e:
                            self._logger.log(
                                f"Heartbeat error for client {client.get_net_id()} -> {e}",
                                Logger.CRITICAL,
                            )
        except asyncio.CancelledError:
            self._logger.log("Heartbeat loop cancelled.", Logger.INFO)
            await self.stop()

    def set_ssl_files(self, certfile: str, keyfile: str):
        """
        Set SSL certificate and key files.
        """
        self.certfile = certfile
        self.keyfile = keyfile

    def _get_ssl_context(self) -> Optional[ssl.SSLContext]:
        """
        Create SSL context if certfile and keyfile are provided.
        """
        if not self.certfile or not self.keyfile:
            return None

        context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        context.load_cert_chain(certfile=self.certfile, keyfile=self.keyfile)
        return context

    def _ssl_wrap(self, client_socket):
        """Wrap del socket con SSL"""
        context = self._get_ssl_context()
        if context is None:
            return client_socket
        ssl_socket = context.wrap_socket(client_socket, server_side=True)
        return ssl_socket
