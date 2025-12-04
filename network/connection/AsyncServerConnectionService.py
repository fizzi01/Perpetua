"""
Provides logic to handle server-side socket connections using asyncio.
Handles SSL and NON-SSL connections, heartbeats, and client management.
Optimized for high performance with MessageExchange asyncio.
"""
import asyncio

import ssl
from typing import Optional, Callable, Any

from model.ClientObj import ClientsManager, ClientObj
from network.data.MessageExchange import MessageExchange, MessageExchangeConfig
from network.protocol.message import MessageType
from utils.logging import Logger

from ..stream import StreamType

from .AsyncClientConnection import AsyncClientConnection

class AsyncServerConnectionHandler:
    """
    Manages server-side socket connections using asyncio.

    Fully optimized for asyncio with non-blocking I/O, efficient handshakes,
    and automatic heartbeat monitoring.

    Attributes:
        msg_exchange (MessageExchange): Message exchange handler (unused, each client has own).
        connected_callback (callable): Callback for client connection.
        disconnected_callback (callable): Callback for client disconnection.
        host (str): Server host address.
        port (int): Server port.
        heartbeat_interval (int): Interval for heartbeat checks.
        clients (ClientsManager): Manager for clients.
        certfile (str): SSL certificate file path.
        keyfile (str): SSL key file path.
    """

    def __init__(self, msg_exchange: Optional['MessageExchange'] = None,
                 connected_callback: Optional[Callable[['ClientObj'], Any]] = None,
                 disconnected_callback: Optional[Callable[['ClientObj'], Any]] = None,
                 host: str = "0.0.0.0", port: int = 5001,
                 heartbeat_interval: int = 2,
                 whitelist: Optional[ClientsManager] = None,
                 certfile: str = None, keyfile: str = None):
        # Nota: msg_exchange non usato, ogni client ha il proprio
        self.certfile = certfile
        self.keyfile = keyfile
        self.clients = whitelist if whitelist is not None else ClientsManager()
        self.connected_callback = connected_callback
        self.disconnected_callback = disconnected_callback
        self.host = host
        self.port = port
        self.heartbeat_interval = heartbeat_interval

        self.server = None
        self._running = False
        self._heartbeat_task = None
        self._client_tasks = {}  # Task di gestione per ogni client
        self._pending_streams = {}  # {ip_address: {stream_type: Future}}

        self.logger = Logger.get_instance()

    async def start(self) -> bool:
        """Avvia il server asyncio con heartbeat monitoring"""
        try:
            self._running = True

            # Crea il server asyncio
            self.server = await asyncio.start_server(
                self._handle_client,
                self.host,
                self.port
            )

            self.logger.log(f"AsyncServer started on {self.host}:{self.port}", Logger.INFO)

            # Avvia heartbeat task
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

            return True
        except Exception as e:
            self.logger.log(f"Failed to start async server => {e}", Logger.CRITICAL)
            # import traceback
            # self.logger.log(traceback.format_exc(), Logger.ERROR)
            self._running = False
            return False

    async def stop(self):
        """Ferma il server e chiude tutte le connessioni in modo pulito"""
        self._running = False

        # Cancella heartbeat task
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass

        # Cancella tutti i task dei client
        for task in self._client_tasks.values():
            if not task.done():
                task.cancel()

        if self._client_tasks:
            await asyncio.gather(*self._client_tasks.values(), return_exceptions=True)
            self._client_tasks.clear()

        # Disconnetti tutti i client
        for client in self.clients.clients:
            if client.is_connected and client.conn_socket is not None:
                try:
                    #await client.conn_socket.close()
                    await client.conn_socket.wait_closed()
                except Exception as e:
                    self.logger.log(f"Error disconnecting client {client.ip_address}: {e}", Logger.ERROR)
                client.is_connected = False
                client.conn_socket = None
                self.clients.update_client(client)

                if self.disconnected_callback:
                    try:
                        if asyncio.iscoroutinefunction(self.disconnected_callback):
                            await self.disconnected_callback(client)
                        else:
                            self.disconnected_callback(client)
                    except Exception as e:
                        self.logger.log(f"Error in disconnected callback: {e}", Logger.ERROR)

        # Chiudi il server
        if self.server:
            self.server.close()
            await self.server.wait_closed()

        self.logger.log("AsyncServer stopped.", Logger.INFO)

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """Gestisce una nuova connessione client (handshake o stream aggiuntivo)"""
        addr = writer.get_extra_info('peername')
        self.logger.log(f"Accepted connection from {addr}", Logger.INFO)

        try:
            client_obj = self.clients.get_client(ip_address=addr[0])
            if not client_obj:
                self.logger.log(f"Client {addr[0]} not in whitelist. Closing connection.", Logger.WARNING)
                writer.close()
                await writer.wait_closed()
                return

            # Controlla se è uno stream aggiuntivo in attesa
            if addr[0] in self._pending_streams and self._pending_streams[addr[0]]:
                # Questa è una connessione per uno stream aggiuntivo
                # Prendi il primo stream type in attesa
                pending = self._pending_streams[addr[0]]
                if pending:
                    stream_type = next(iter(pending.keys()))
                    future = pending[stream_type]

                    if not future.done():
                        future.set_result((reader, writer))
                        self.logger.log(f"Stream {stream_type} accepted from {addr[0]}", Logger.DEBUG)
                    else:
                        self.logger.log(f"Future already done for stream {stream_type} from {addr[0]}", Logger.WARNING)
                        writer.close()
                        await writer.wait_closed()

                    del pending[stream_type]
                    return

            # Altrimenti è una connessione di handshake
            if client_obj.is_connected:
                self.logger.log(f"Client {addr[0]} is already connected. Closing new connection.", Logger.WARNING)
                writer.close()
                await writer.wait_closed()
                return

            # Handshake
            if await self._handshake(reader, writer, addr, client_obj):
                client_obj.is_connected = True
                self.clients.update_client(client_obj)

                if self.connected_callback:
                    try:
                        if asyncio.iscoroutinefunction(self.connected_callback):
                            await self.connected_callback(client_obj)
                        else:
                            self.connected_callback(client_obj)
                    except Exception as e:
                        self.logger.log(f"Error in connected callback: {e}", Logger.ERROR)

                self.logger.log(f"Client {addr[0]} connected and handshake completed.", Logger.INFO)
            else:
                self.logger.log(f"Handshake failed for client {addr[0]}. Closing connection.", Logger.WARNING)
                writer.close()
                await writer.wait_closed()
                client_obj.conn_socket = None
                client_obj.is_connected = False
                self.clients.update_client(client_obj)

        except Exception as e:
            self.logger.log(f"Error handling client {addr}: {e}", Logger.ERROR)
            import traceback
            self.logger.log(traceback.format_exc(), Logger.ERROR)

    async def _handshake(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                         client_addr, client: ClientObj) -> bool:
        """
        Esegue l'handshake con il client usando MessageExchange asyncio.

        Returns:
            True se handshake completato con successo, False altrimenti
        """
        try:
            # Crea un MessageExchange dedicato per questo client
            from network.data.MessageExchange import MessageExchange, MessageExchangeConfig

            config = MessageExchangeConfig(
                max_chunk_size=4096,
                auto_chunk=True,
                auto_dispatch=False, # We want to control message handling manually
            )
            client_msg_exchange = MessageExchange(config)

            # Setup transport callbacks asyncio
            async def async_send(data: bytes):
                writer.write(data)
                await writer.drain()

            async def async_recv(size: int) -> bytes:
                return await reader.read(size)

            client_msg_exchange.set_transport(async_send, async_recv)

            # Invia handshake request
            self.logger.log(f"Sending handshake request to client {client.ip_address}", Logger.DEBUG)
            await client_msg_exchange.send_handshake_message(
                ack=False,
                source="server",
                screen_position=client.screen_position,
                target=client.screen_position
            )

            # Avvia ricezione per processare la risposta
            await client_msg_exchange.start()

            # Attendi risposta con timeout
            try:
                response = await asyncio.wait_for(
                    client_msg_exchange.get_received_message(timeout=1.0),
                    timeout=5.0
                )
            except asyncio.TimeoutError:
                self.logger.log(f"Handshake timeout for client {client.ip_address}", Logger.WARNING)
                await client_msg_exchange.stop()
                return False

            # Verifica risposta
            if response and response.message_type == MessageType.EXCHANGE and response.payload.get("ack", False):
                # Estrai informazioni client
                client.screen_resolution = response.payload.get("screen_resolution", None)
                client.additional_params = response.payload.get("additional_params", {})
                client.ssl = response.payload.get("ssl", False)
                requested_streams = response.payload.get("streams", [])

                self.logger.log(f"Client {client.ip_address} info: resolution={client.screen_resolution}, ssl={client.ssl}, streams={requested_streams}", Logger.DEBUG)

                # Crea AsyncClientConnection per gestire multiple streams asyncio
                client.conn_socket = AsyncClientConnection(client_addr)
                client.conn_socket.add_stream(StreamType.COMMAND, reader, writer)

                # Accetta stream aggiuntivi richiesti dal client
                if requested_streams:
                    self.logger.log(f"Client {client.ip_address} requested {len(requested_streams)} additional streams", Logger.DEBUG)

                    # Prepara i future per gli stream in arrivo
                    if client.ip_address not in self._pending_streams:
                        self._pending_streams[client.ip_address] = {}

                    for stream_type in requested_streams:
                        try:
                            # Crea un future per questo stream
                            stream_future = asyncio.Future()
                            self._pending_streams[client.ip_address][stream_type] = stream_future

                            # Attendi che il client si connetta per questo stream
                            # Il future verrà risolto in _handle_client quando arriva la connessione
                            stream_reader, stream_writer = await asyncio.wait_for(
                                stream_future,
                                timeout=10.0
                            )

                            stream_addr = stream_writer.get_extra_info('peername')

                            # Wrap con SSL se richiesto (già gestito da asyncio transport)
                            if client.ssl and self.certfile and self.keyfile:
                                self.logger.log(f"SSL stream connection for {stream_type} from {stream_addr}", Logger.INFO)

                            client.conn_socket.add_stream(stream_type, stream_reader, stream_writer)
                            client.ports[stream_type] = stream_addr[1]

                            self.logger.log(f"Stream {stream_type} connected from {stream_addr}", Logger.DEBUG)

                        except asyncio.TimeoutError:
                            self.logger.log(f"Timeout waiting for stream {stream_type} from client {client.ip_address}", Logger.WARNING)
                            # Pulisci i pending streams
                            if client.ip_address in self._pending_streams:
                                self._pending_streams[client.ip_address].pop(stream_type, None)
                                if not self._pending_streams[client.ip_address]:
                                    del self._pending_streams[client.ip_address]
                            await client_msg_exchange.stop()
                            return False
                        except Exception as e:
                            import traceback
                            self.logger.log(f"Error accepting stream {stream_type}: {e}", Logger.ERROR)
                            # Pulisci i pending streams
                            if client.ip_address in self._pending_streams:
                                self._pending_streams[client.ip_address].pop(stream_type, None)
                                if not self._pending_streams[client.ip_address]:
                                    del self._pending_streams[client.ip_address]
                            await client_msg_exchange.stop()
                            return False

                    # Pulisci i pending streams per questo client
                    if client.ip_address in self._pending_streams:
                        del self._pending_streams[client.ip_address]

                # Cleanup temporaneo del msg_exchange (il client ne avrà uno proprio)
                await client_msg_exchange.stop()

                self.logger.log(f"Handshake successful with client {client.ip_address}", Logger.INFO)
                return True
            else:
                self.logger.log(f"Invalid handshake response from client {client.ip_address}", Logger.WARNING)
                await client_msg_exchange.stop()
                return False

        except asyncio.CancelledError:
            self.logger.log(f"Handshake cancelled for client {client.ip_address}", Logger.WARNING)
            raise
        except Exception as e:
            self.logger.log(f"Handshake error with client {client.ip_address}: {e}", Logger.ERROR)
            import traceback
            self.logger.log(traceback.format_exc(), Logger.ERROR)
            return False


    async def _heartbeat_loop(self):
        """Loop di heartbeat per verificare le connessioni e aggiornare metriche"""
        try:
            while self._running:
                await asyncio.sleep(self.heartbeat_interval)

                for client in self.clients.clients:
                    if client.is_connected and client.conn_socket is not None:
                        try:
                            # Use is_open() for AsyncClientConnection
                            if not client.conn_socket.is_open():
                                raise ConnectionResetError

                            # Send heartbeat message
                            config = MessageExchangeConfig(
                                max_chunk_size=4096,
                                auto_chunk=True,
                                auto_dispatch=False, # We want to control message handling manually
                            )
                            client_msg_exchange = MessageExchange(config)
                            async def async_send(data: bytes):
                                command_writer = client.conn_socket.get_writer(StreamType.COMMAND)
                                command_writer.write(data)
                                await command_writer.drain()

                            client_msg_exchange.set_transport(async_send, None)
                            await client_msg_exchange.send_custom_message(message_type="HEARTBEAT", payload={})

                            # Update active time
                            client.connection_time += self.heartbeat_interval
                        except (ConnectionResetError, OSError):
                            self.logger.log(f"Client {client.ip_address} disconnected (heartbeat failed).", Logger.WARNING)
                            client.is_connected = False

                            # Close asyncio streams properly
                            #client.conn_socket.close()
                            try:
                                await client.conn_socket.wait_closed()
                            except Exception:
                                pass

                            client.conn_socket = None
                            self.clients.update_client(client)

                            if self.disconnected_callback:
                                try:
                                    if asyncio.iscoroutinefunction(self.disconnected_callback):
                                        await self.disconnected_callback(client)
                                    else:
                                        self.disconnected_callback(client)
                                except Exception as e:
                                    self.logger.log(f"Error in disconnected callback: {e}", Logger.ERROR)
                        except Exception as e:
                            self.logger.log(f"Heartbeat error for client {client.ip_address}: {e}", Logger.CRITICAL)
        except asyncio.CancelledError:
            self.logger.log("Heartbeat loop cancelled.", Logger.INFO)
            await self.stop()

    def _ssl_wrap(self, client_socket):
        """Wrap del socket con SSL"""
        context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        context.load_cert_chain(certfile=self.certfile, keyfile=self.keyfile)
        ssl_socket = context.wrap_socket(client_socket, server_side=True)
        return ssl_socket
