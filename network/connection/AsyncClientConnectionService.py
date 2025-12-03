"""
Async Client Connection Handler using asyncio.
Optimized for MessageExchange asyncio compatibility.
"""
import asyncio
import ssl
from typing import Optional, Callable, Any

from model.ClientObj import ClientsManager, ClientObj
from network.connection.AsyncClientConnection import AsyncClientConnection
from network.exceptions.ConnectionExceptions import ServerNotFoundException
from network.data.MessageExchange import MessageExchange, MessageExchangeConfig
from network.protocol.message import MessageType
from network.stream import StreamType
from utils.logging import Logger


class AsyncClientConnectionHandler:
    """
    Async client-side connection handler using asyncio.

    Manages connections to a server with handshake, multiple streams,
    heartbeat monitoring, and automatic reconnection.

    Fully optimized for asyncio with non-blocking I/O and efficient
    resource management.
    """

    def __init__(self,
                 connected_callback: Optional[Callable[['ClientObj'], Any]] = None,
                 disconnected_callback: Optional[Callable[['ClientObj'], Any]] = None,
                 host: str = "127.0.0.1",
                 port: int = 5001,
                 wait: int = 5,
                 heartbeat_interval: int = 10,
                 max_errors: int = 10,
                 clients: Optional[ClientsManager] = None,
                 open_streams: list[int] = None,
                 certfile: str = None,
                 auto_reconnect: bool = True):
        """
        Initialize AsyncClientConnectionHandler.

        Args:
            connected_callback: Callback when connected to server (can be async)
            disconnected_callback: Callback when disconnected from server (can be async)
            host: Server host address
            port: Server port
            wait: Wait time between connection attempts (seconds)
            heartbeat_interval: Interval for heartbeat checks (seconds)
            max_errors: Maximum consecutive errors before stopping
            clients: ClientsManager instance
            open_streams: List of stream types to open (default: MOUSE, KEYBOARD, CLIPBOARD)
            certfile: Path to SSL certificate file
            auto_reconnect: Automatically reconnect on disconnection
        """
        self.connected_callback = connected_callback
        self.disconnected_callback = disconnected_callback

        self.host = host
        self.port = port
        self.wait = wait
        self.max_errors = max_errors
        self.heartbeat_interval = heartbeat_interval
        self.auto_reconnect = auto_reconnect

        self.certfile = certfile
        self.use_ssl = bool(certfile)

        self.clients = clients if clients is not None else ClientsManager(client_mode=True)

        self.open_streams = open_streams if open_streams is not None else [
            StreamType.MOUSE,
            StreamType.KEYBOARD,
            StreamType.CLIPBOARD
        ]

        # Connection state
        self._running = False
        self._connected = False

        # Asyncio components
        self._core_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None

        # Streams
        self._command_reader: Optional[asyncio.StreamReader] = None
        self._command_writer: Optional[asyncio.StreamWriter] = None
        self._stream_readers: dict = {}
        self._stream_writers: dict = {}

        # MessageExchange (uno per client)
        self._msg_exchange: Optional[MessageExchange] = None

        # Client object
        self._client_obj: Optional[ClientObj] = None

        self.logger = Logger.get_instance()

    async def start(self) -> bool:
        """Start the async client connection handler"""
        try:
            if self._running:
                self.logger.log("AsyncClientConnectionHandler already running", Logger.WARNING)
                return False

            self._running = True

            # Initialize client object
            self._client_obj = self.clients.get_client(ip_address=self.host)
            if not self._client_obj:
                self._client_obj = ClientObj(
                    ip_address=self.host,
                    ssl=self.use_ssl,
                    screen_position="unknown"
                )
                self.clients.add_client(self._client_obj)

            self._client_obj.ssl = self.use_ssl
            self.clients.update_client(self._client_obj)

            # Start core connection loop
            self._core_task = asyncio.create_task(self._core_loop())

            self.logger.log("AsyncClientConnectionHandler started", Logger.INFO)
            return True

        except Exception as e:
            self.logger.log(f"Failed to start AsyncClientConnectionHandler: {e}", Logger.ERROR)
            import traceback
            self.logger.log(traceback.format_exc(), Logger.ERROR)
            self._running = False
            return False

    async def stop(self):
        """Stop the handler and close all connections"""
        self._running = False

        # Cancel core task
        if self._core_task and not self._core_task.done():
            self._core_task.cancel()
            try:
                await self._core_task
            except asyncio.CancelledError:
                pass

        # Cancel heartbeat task
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass

        # Close all connections
        await self._close_all_streams()

        # Stop message exchange
        if self._msg_exchange:
            await self._msg_exchange.stop()

        # Update client status
        if self._client_obj:
            self._client_obj.is_connected = False
            self.clients.update_client(self._client_obj)

        self.logger.log("AsyncClientConnectionHandler stopped", Logger.INFO)

    async def _core_loop(self):
        """Main connection loop with automatic reconnection"""
        error_count = 0

        while self._running:
            try:
                if not self._connected:
                    self.logger.log(f"Attempting to connect to {self.host}:{self.port}...", Logger.INFO)

                    # Attempt connection
                    if await self._connect():
                        self.logger.log("Connection established, performing handshake...", Logger.INFO)

                        # Set first client connection socket
                        client = self.clients.get_client()
                        client.conn_socket = AsyncClientConnection(("",0)) #TODO: Better initialization
                        client.conn_socket.add_stream(StreamType.COMMAND, self._command_reader, self._command_writer)
                        self.clients.update_client(client)

                        # Perform handshake
                        if await self._handshake():
                            self._connected = True
                            error_count = 0

                            self.logger.log("Handshake successful, client connected", Logger.INFO)

                            # Update client status
                            self._client_obj.is_connected = True
                            self.clients.update_client(self._client_obj)

                            await self._msg_exchange.stop() # Stop to enable stream handlers

                            # Call connected callback
                            if self.connected_callback:
                                try:
                                    if asyncio.iscoroutinefunction(self.connected_callback):
                                        await self.connected_callback(self._client_obj)
                                    else:
                                        self.connected_callback(self._client_obj)
                                except Exception as e:
                                    self.logger.log(f"Error in connected callback: {e}", Logger.ERROR)

                            # Start heartbeat monitoring
                            if not self._heartbeat_task or self._heartbeat_task.done():
                                self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
                        else:
                            self.logger.log("Handshake failed", Logger.ERROR)
                            await self._close_all_streams()
                            await asyncio.sleep(self.wait)
                            continue
                    else:
                        # Connection failed
                        error_count += 1
                        if error_count >= self.max_errors:
                            self.logger.log("Max connection errors reached, stopping", Logger.ERROR)
                            self._running = False
                            break

                        await asyncio.sleep(self.wait)
                        continue

                # Connection is established, just wait
                await asyncio.sleep(self.heartbeat_interval)

            except asyncio.CancelledError:
                self.logger.log("Core loop cancelled", Logger.DEBUG)
                break
            except Exception as e:
                self.logger.log(f"Error in core loop: {e}", Logger.ERROR)
                import traceback
                self.logger.log(traceback.format_exc(), Logger.ERROR)

                # Handle disconnection
                if self._connected:
                    await self._handle_disconnection()

                error_count += 1
                if error_count >= self.max_errors and not self.auto_reconnect:
                    self.logger.log("Max errors reached and auto_reconnect disabled, stopping", Logger.ERROR)
                    self._running = False
                    break

                await asyncio.sleep(self.wait)

        self._connected = False

    async def _connect(self) -> bool:
        """Establish command stream connection"""
        try:
            # Create SSL context if needed
            ssl_context = None
            if self.use_ssl and self.certfile:
                ssl_context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
                ssl_context.load_verify_locations(self.certfile)

            # Connect to server
            self._command_reader, self._command_writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port, ssl=ssl_context),
                timeout=5.0
            )

            self.logger.log(f"Connected to {self.host}:{self.port}", Logger.DEBUG)
            return True

        except asyncio.TimeoutError:
            self.logger.log(f"Connection timeout to {self.host}:{self.port}", Logger.WARNING)
            return False
        except ConnectionRefusedError:
            self.logger.log(f"Connection refused by {self.host}:{self.port}", Logger.WARNING)
            return False
        except Exception as e:
            self.logger.log(f"Connection error: {e}", Logger.ERROR)
            return False

    async def _handshake(self) -> bool:
        """Perform handshake with server using MessageExchange asyncio"""
        try:
            # Create MessageExchange for this client
            config = MessageExchangeConfig(
                max_chunk_size=4096,
                auto_chunk=True,
                auto_dispatch=False,  # We want to control message handling manually
            )
            self._msg_exchange = MessageExchange(config)

            # Setup transport callbacks
            async def async_send(data: bytes):
                self._command_writer.write(data)
                await self._command_writer.drain()

            async def async_recv(size: int) -> bytes:
                return await self._command_reader.read(size)

            self._msg_exchange.set_transport(async_send, async_recv)

            # Start receive loop
            await self._msg_exchange.start()

            # Wait for handshake request from server
            self.logger.log("Waiting for handshake request from server...", Logger.DEBUG)

            handshake_req = await asyncio.wait_for(
                self._msg_exchange.get_received_message(timeout=1.0),
                timeout=5.0
            )

            if not handshake_req or handshake_req.message_type != MessageType.EXCHANGE:
                self.logger.log("Invalid handshake request", Logger.ERROR)
                return False

            if handshake_req.source != "server":
                self.logger.log(f"Handshake source is not server: {handshake_req.source}", Logger.ERROR)
                return False

            self.logger.log("Received valid handshake request from server", Logger.DEBUG)

            # Update client info from handshake
            self._client_obj.screen_position = handshake_req.payload.get("screen_position", "unknown")

            # Send handshake response
            await self._msg_exchange.send_handshake_message(
                ack=True,
                source=self._client_obj.screen_position,
                target="server",
                streams=self.open_streams,
                screen_position=self._client_obj.screen_position,
                screen_resolution=self._client_obj.screen_resolution,
                ssl=self._client_obj.ssl,
            )

            self.logger.log("Sent handshake response to server", Logger.DEBUG)

            # Small delay to ensure server processes handshake
            await asyncio.sleep(0.2)

            # Open additional streams
            if self.open_streams:
                success = await self._open_additional_streams()
                if not success:
                    self.logger.log("Failed to open additional streams", Logger.ERROR)
                    return False

                # Add additional streams to client connection socket
                for stream_type in self.open_streams:
                    reader = self._stream_readers.get(stream_type)
                    writer = self._stream_writers.get(stream_type)
                    if reader and writer:
                        client = self.clients.get_client()
                        client.conn_socket.add_stream(stream_type, reader, writer)
                        self.clients.update_client(client)

            self.logger.log("Handshake completed successfully", Logger.INFO)
            return True

        except asyncio.TimeoutError:
            self.logger.log("Handshake timeout", Logger.ERROR)
            return False
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.logger.log(f"Handshake error: {e}", Logger.ERROR)
            import traceback
            self.logger.log(traceback.format_exc(), Logger.ERROR)
            return False

    async def _open_additional_streams(self) -> bool:
        """Open additional streams requested in handshake"""
        ssl_context = None
        if self.use_ssl and self.certfile:
            ssl_context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
            ssl_context.load_verify_locations(self.certfile)

        for stream_type in self.open_streams:
            try:
                # Connect to server for this stream
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(self.host, self.port, ssl=ssl_context),
                    timeout=10.0
                )

                self._stream_readers[stream_type] = reader
                self._stream_writers[stream_type] = writer

                self.logger.log(f"Stream {stream_type} connected", Logger.DEBUG)

            except asyncio.TimeoutError:
                self.logger.log(f"Timeout connecting stream {stream_type}", Logger.ERROR)
                return False
            except Exception as e:
                self.logger.log(f"Failed to connect stream {stream_type}: {e}", Logger.ERROR)
                return False

        return True

    async def _heartbeat_loop(self):
        """Monitor connection health"""
        while self._running and self._connected:
            try:
                await asyncio.sleep(self.heartbeat_interval)

                # Check if command stream is still alive
                if not self._command_writer or self._command_writer.is_closing():
                    raise ConnectionResetError("Command stream closed")

                # Send heartbeat message
                await self._msg_exchange.send_custom_message(message_type="HEARTBEAT", payload={})


            except asyncio.CancelledError:
                break
            except ConnectionResetError:
                self.logger.log("Heartbeat detected disconnection", Logger.WARNING)
                await self._handle_disconnection()
                break
            except Exception as e:
                self.logger.log(f"Heartbeat error: {e}", Logger.ERROR)
                await self._handle_disconnection()
                break

    async def _handle_disconnection(self):
        """Handle disconnection and cleanup"""
        self._connected = False

        # Close all streams
        await self._close_all_streams()

        # Stop message exchange
        if self._msg_exchange:
            await self._msg_exchange.stop()
            self._msg_exchange = None

        # Update client status
        if self._client_obj:
            self._client_obj.is_connected = False
            self.clients.update_client(self._client_obj)

        # Call disconnected callback
        if self.disconnected_callback:
            try:
                if asyncio.iscoroutinefunction(self.disconnected_callback):
                    await self.disconnected_callback(self._client_obj)
                else:
                    self.disconnected_callback(self._client_obj)
            except Exception as e:
                self.logger.log(f"Error in disconnected callback: {e}", Logger.ERROR)

        self.logger.log("Client disconnected from server", Logger.WARNING)

    async def _close_all_streams(self):
        """Close all stream connections"""
        # Close command stream
        if self._command_writer and not self._command_writer.is_closing():
            self._command_writer.close()
            try:
                await self._command_writer.wait_closed()
            except Exception:
                pass

        self._command_reader = None
        self._command_writer = None

        # Close additional streams
        for stream_type, writer in list(self._stream_writers.items()):
            if writer and not writer.is_closing():
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass

        self._stream_readers.clear()
        self._stream_writers.clear()

    def is_connected(self) -> bool:
        """Check if client is connected"""
        return self._connected

    async def send_message(self, message_type: int, **kwargs):
        """
        Send a message through the appropriate stream.

        Args:
            message_type: StreamType constant
            **kwargs: Message parameters
        """
        if not self._connected or not self._msg_exchange:
            raise ConnectionError("Not connected to server")

        await self._msg_exchange.send_stream_type_message(message_type, **kwargs)

