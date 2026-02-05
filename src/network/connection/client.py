"""
Client-side connection Handler
"""


#  Perpetua - open-source and cross-platform KVM software.
#  Copyright (c) 2026 Federico Izzi.
#
#  This program is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program.  If not, see <https://www.gnu.org/licenses/>.
#

import asyncio
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
    Async client-side connection handler using asyncio.

    Manages connections to a server with handshake, multiple streams,
    heartbeat monitoring, and automatic reconnection.

    Fully optimized for asyncio with non-blocking I/O and efficient
    resource management.
    """

    CONNECTION_ATTEMPT_TIMEOUT = 10  # seconds
    RECONNECTION_DELAY = 10  # seconds
    HANDSHAKE_DELAY = 0.5  # seconds
    STREAM_CONN_DELAY_GUARD = 1  # seconds
    HANDSHAKE_MSG_TIMEOUT = 5.0  # seconds
    MAX_HEARTBEAT_MISSES = 1

    def __init__(
        self,
        connected_callback: Optional[Callable[["ClientObj"], Any]] = None,
        disconnected_callback: Optional[Callable[["ClientObj"], Any]] = None,
        reconnected_callback: Optional[Callable[["ClientObj", list[int]], Any]] = None,
        host: str = "127.0.0.1",
        port: int = 5001,
        wait: int = 5,
        heartbeat_interval: int = 10,
        max_errors: int = 10,
        clients: Optional[ClientsManager] = None,
        open_streams: Optional[list[int]] = None,
        certfile: Optional[str] = None,
        use_ssl: bool = False,
        auto_reconnect: bool = True,
    ):
        """
        Manages client connections to server.

        Args:
            connected_callback: Callback when connected to server (can be async)
            disconnected_callback: Callback when disconnected from server (can be async)
            reconnected_callback: Callback when reconnected to server (can be async)
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
        self.reconnected_callback = reconnected_callback

        self.host = host
        self.port = port
        self.wait = wait
        self.max_errors = max_errors
        self.heartbeat_interval = heartbeat_interval
        self.auto_reconnect = auto_reconnect

        self.certfile = certfile
        self.use_ssl = use_ssl

        if self.use_ssl and self.certfile is None:
            raise ValueError("SSL is enabled but no certificate file provided")

        self.clients = (
            clients if clients is not None else ClientsManager(client_mode=True)
        )

        self.open_streams = open_streams if open_streams is not None else []

        # Connection state
        self._running = False
        self._connected = False

        # Asyncio components
        self._core_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None

        # Streams
        self._command_stream: Optional[StreamWrapper] = None

        # MessageExchange
        self._msg_exchange: Optional[MessageExchange] = None

        # Client object
        self._client_obj: Optional[ClientObj] = None

        self._logger = get_logger(self.__class__.__name__)

    async def start(self) -> bool:
        """Start the async client connection handler"""
        try:
            if self._running:
                self._logger.log("Already running", Logger.WARNING)
                return False

            self._running = True

            # Initialize client object
            self._client_obj = self.clients.get_client()
            if not self._client_obj:
                raise Exception("Missing client object in ClientsManager")

            self._client_obj.ssl = self.use_ssl
            self.clients.update_client(self._client_obj)

            # Start core connection loop
            self._core_task = asyncio.create_task(self._core_loop())

            self._logger.log("Started", Logger.INFO)
            return True

        except Exception as e:
            self._logger.log(f"Failed to start -> {e}", Logger.ERROR)
            import traceback

            self._logger.log(traceback.format_exc(), Logger.ERROR)
            self._running = False
            return False

    async def stop(self):
        """Stop the handler and close all connections"""
        if not self._running:  # Already stopped
            return True

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

        self._logger.log("Stopped", Logger.INFO)
        return True

    async def _core_loop(self):
        """Main connection loop with automatic reconnection"""
        error_count = 0

        while self._running:
            try:
                if not self._connected:
                    self._logger.log(
                        f"Attempting to connect to {self.host}:{self.port}...",
                        Logger.INFO,
                    )

                    # Attempt connection
                    if await self._connect():
                        self._logger.log(
                            "Connection established, performing handshake...",
                            Logger.INFO,
                        )

                        # Set first client connection socket
                        if self._client_obj is None:
                            raise Exception("Client not connected")

                        conn = ClientConnection(("", 0))
                        conn.add_stream(
                            stream_type=StreamType.COMMAND, stream=self._command_stream
                        )
                        self._client_obj.set_connection(connection=conn)
                        self.clients.update_client(self._client_obj)

                        # Perform handshake
                        if await self._handshake():
                            self._connected = True
                            error_count = 0

                            self._logger.log(
                                "Handshake successful, client connected", Logger.INFO
                            )

                            # Update client status
                            self._client_obj.set_connection_status(status=True)
                            if self._command_stream is None:
                                raise Exception(
                                    "Command stream is None after handshake"
                                )

                            self._client_obj.ip_address = (
                                self._command_stream.get_sockname()[0]
                            )
                            self.clients.update_client(self._client_obj)

                            # Call connected callback
                            if self.connected_callback:
                                try:
                                    await self._invoke_callback(
                                        callback=self.connected_callback,
                                        client=self._client_obj,
                                    )
                                except CallbackError as e:
                                    self._logger.log(
                                        f"Error in connected callback -> {e}",
                                        Logger.ERROR,
                                    )

                            # Start heartbeat monitoring
                            if not self._heartbeat_task or self._heartbeat_task.done():
                                self._heartbeat_task = asyncio.create_task(
                                    self._heartbeat_loop()
                                )
                        else:
                            self._logger.log("Handshake failed", Logger.ERROR)
                            await self._close_all_streams()
                            await asyncio.sleep(self.wait)
                            continue
                    else:
                        # Connection failed
                        error_count += 1
                        if error_count >= self.max_errors and self.auto_reconnect:
                            self._logger.log(
                                "Max connection errors reached, going sleep mode",
                                Logger.ERROR,
                            )
                            await asyncio.sleep(
                                self.RECONNECTION_DELAY
                            )  # TODO: Implement backoff strategy
                            # Optionally stop trying to reconnect
                            # self._running = False
                            # break
                        elif error_count >= self.max_errors:
                            # No auto reconnect, stop
                            raise Exception("Max connection errors reached")

                        await asyncio.sleep(self.wait)
                        continue

                # Connection is established, just wait
                await asyncio.sleep(self.heartbeat_interval)

            except asyncio.CancelledError:
                self._logger.log("Core loop cancelled", Logger.DEBUG)
                self._connected = False
                await self.stop()
                break
            except Exception as e:
                self._logger.log(f"Error in core loop -> {e}", Logger.ERROR)
                import traceback

                self._logger.log(traceback.format_exc(), Logger.ERROR)

                # Handle disconnection
                if self._connected:
                    await self._handle_disconnection()

                error_count += 1
                if error_count >= self.max_errors and not self.auto_reconnect:
                    self._logger.log(
                        "Max errors reached and auto_reconnect disabled, stopping",
                        Logger.ERROR,
                    )
                    self._running = False
                    break

                await asyncio.sleep(self.wait)

        self._connected = False

    async def _connect(self) -> bool:
        """Establish command stream connection"""
        try:
            # Connect to server
            _command_reader, _command_writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port),
                timeout=self.CONNECTION_ATTEMPT_TIMEOUT,
            )
            self._command_stream = StreamWrapper(
                reader=_command_reader, writer=_command_writer
            )

            self._logger.log(f"Connected to {self.host}:{self.port}", Logger.DEBUG)
            return True

        except asyncio.TimeoutError:
            self._logger.log(
                f"Connection timeout to {self.host}:{self.port}", Logger.WARNING
            )
            return False
        except ConnectionRefusedError:
            self._logger.log(
                f"Connection refused by {self.host}:{self.port}", Logger.WARNING
            )
            return False
        except Exception as e:
            self._logger.log(f"Connection error -> {e}", Logger.ERROR)
            return False

    async def _handshake(self) -> bool:
        """Perform handshake with server"""
        try:
            # Create MessageExchange for this client
            config = MessageExchangeConfig(
                max_chunk_size=4096,
                auto_chunk=True,
                auto_dispatch=False,  # We want to control message handling manually
            )
            self._msg_exchange = MessageExchange(config)
            if self._command_stream is None:
                raise Exception("Command stream is None during handshake")

            await self._msg_exchange.set_transport(
                self._command_stream.get_writer_call(),
                self._command_stream.get_reader_call(),
            )

            # Start receive loop
            await self._msg_exchange.start()

            # Wait for handshake request from server
            self._logger.log(
                "Waiting for handshake request from server...", Logger.DEBUG
            )

            handshake_req = await asyncio.wait_for(
                self._msg_exchange.get_received_message(),
                timeout=self.HANDSHAKE_MSG_TIMEOUT,
            )

            if not handshake_req or handshake_req.message_type != MessageType.EXCHANGE:
                self._logger.log("Invalid handshake request", Logger.ERROR)
                return False

            if handshake_req.source != "server":
                self._logger.log(
                    f"Handshake source is not server: {handshake_req.source}",
                    Logger.ERROR,
                )
                return False

            self._logger.log(
                "Received valid handshake request from server", Logger.DEBUG
            )

            if self._client_obj is None:
                raise Exception("Client object is None during handshake")

            # Send handshake response
            await self._msg_exchange.send_handshake_message(
                ack=True,
                client_name=self._client_obj.uid,
                source=self._client_obj.host_name,
                target="server",
                streams=self.open_streams,
                screen_position=self._client_obj.screen_position,
                screen_resolution=self._client_obj.screen_resolution,
                ssl=self.use_ssl,
            )

            self._logger.debug(
                "Sent handshake response to server", streams=self.open_streams
            )

            # Small delay to ensure server processes handshake
            await asyncio.sleep(self.HANDSHAKE_DELAY)

            # Receive handshake acknowledgment from server
            handshake_ack = await asyncio.wait_for(
                self._msg_exchange.get_received_message(),
                timeout=self.HANDSHAKE_MSG_TIMEOUT,
            )
            # Update client info from handshake
            if (
                not handshake_ack
                or handshake_ack.message_type != MessageType.EXCHANGE
                or not handshake_ack.payload.get("ack", False)
            ):
                self._logger.log(
                    "Handshake failed, invalid acknowledgment from server", Logger.ERROR
                )
                return False

            self._client_obj.set_screen_position(
                handshake_ack.payload.get("screen_position", "unknown")
            )

            # Open additional streams
            if self.open_streams:
                success = await self._open_additional_streams(streams=self.open_streams)
                if not success:
                    self._logger.log("Failed to open additional streams", Logger.ERROR)
                    return False

            self._logger.log("Handshake completed successfully", Logger.INFO)
            await self._msg_exchange.stop()
            return True

        except asyncio.TimeoutError:
            self._logger.log("Handshake timeout", Logger.ERROR)
            return False
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._logger.log(f"Handshake error -> {e}", Logger.ERROR)
            import traceback

            self._logger.log(traceback.format_exc(), Logger.ERROR)
            return False

    async def _open_additional_streams(self, streams: list[int]) -> bool:
        """
        Attempts to open additional streams based on the provided stream types and establish
        secure or non-secure connections accordingly while managing connections within
        the client object.

        Args:
            streams (list[int]): A list of integers representing the types of streams to be
                opened and connected. Each stream type should correspond to a valid stream code.

        Returns:
            bool: True if all streams were successfully connected and configured; False if
                any stream connection failed due to a timeout or other issues.
        """
        ssl_context = None
        if self.use_ssl and self.certfile:
            ssl_context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
            ssl_context.load_verify_locations(self.certfile)

        for stream_type in streams:
            try:
                # Connect to server for this stream
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(self.host, self.port),
                    timeout=self.CONNECTION_ATTEMPT_TIMEOUT,
                )

                # Upgrade to TLS if needed
                if self.use_ssl and ssl_context:
                    await asyncio.wait_for(
                        writer.start_tls(sslcontext=ssl_context),
                        timeout=self.CONNECTION_ATTEMPT_TIMEOUT,
                    )

                # Store connected stream readers and writers in ClientConnection
                if self._client_obj is None:
                    raise Exception(
                        "Client object is None when opening additional streams"
                    )
                conn = self._client_obj.get_connection()
                if conn is not None:
                    conn.add_stream(
                        stream_type=stream_type, reader=reader, writer=writer
                    )
                self._client_obj.set_connection(connection=conn)
                self.clients.update_client(self._client_obj)

                self._logger.log(f"Stream {stream_type} connected", Logger.DEBUG)

            except asyncio.TimeoutError:
                self._logger.log(
                    f"Timeout connecting stream {stream_type}", Logger.ERROR
                )
                return False
            except Exception as e:
                self._logger.log(
                    f"Failed to connect stream {stream_type} -> {e}", Logger.ERROR
                )
                return False

        return True

    async def _heartbeat_loop(self):
        """Monitor connection health"""
        heartbeat_trials = 0
        # dbg_b = True
        while self._running and self._connected:
            try:
                await asyncio.sleep(self.heartbeat_interval)

                # Check if command stream is still alive
                if not self._command_stream or not await self._command_stream.is_open():
                    raise ConnectionResetError("Command stream closed")

                # Send heartbeat message
                # await self._msg_exchange.send_custom_message(message_type="HEARTBEAT", payload={})
                # Get reader from client connection and check if eof is reached
                if self._client_obj is None:
                    raise Exception("Client object is None during heartbeat")

                c_conn = self._client_obj.get_connection()
                if c_conn is not None and c_conn.has_stream(StreamType.COMMAND):
                    command_reader = c_conn.get_reader(StreamType.COMMAND)
                    if command_reader is None or command_reader.is_closed():
                        raise ConnectionResetError("Command stream EOF reached")

                # st = c_conn.get_stream(StreamType.MOUSE)
                # if dbg_b:
                #     await st.close()
                # Check others streams to handle reconnection
                closed_streams: list[int] = []
                for stream_type in self.open_streams:
                    if c_conn is not None and c_conn.has_stream(stream_type):
                        stream_reader = c_conn.get_reader(stream_type)
                        stream_writer = c_conn.get_writer(stream_type)
                        if (stream_reader is None or stream_reader.is_closed()) or (
                            stream_writer is None or await stream_writer.is_closed()
                        ):
                            # Force closure of the stream writer if it exists
                            if stream_writer:
                                await stream_writer.close()
                            closed_streams.append(stream_type)

                # Attempt to reopen closed streams
                if len(closed_streams) > 0:
                    self._logger.warning(
                        "Detected closed streams, attempting reconnection...",
                        closed_streams=closed_streams,
                    )
                    await asyncio.sleep(self.STREAM_CONN_DELAY_GUARD)

                    if not await self._open_additional_streams(closed_streams):
                        raise ConnectionResetError("Failed to reopen closed streams")
                    else:
                        try:
                            await self._invoke_callback(
                                callback=self.reconnected_callback,
                                client=self._client_obj,
                                streams=closed_streams,
                            )
                        except CallbackError as e:
                            self._logger.log(
                                f"Error in reconnected callback -> {e}", Logger.ERROR
                            )

            except asyncio.CancelledError:
                break
            except ConnectionResetError as e:
                if heartbeat_trials < self.MAX_HEARTBEAT_MISSES:
                    heartbeat_trials += 1
                    self._logger.log(
                        f"Heartbeat missed {heartbeat_trials}/{self.MAX_HEARTBEAT_MISSES}",
                        Logger.WARNING,
                    )
                    continue
                else:
                    self._logger.log("Heartbeat detected disconnection", Logger.WARNING)
                    await self._handle_disconnection(err=e)
                    break
            except Exception as e:
                self._logger.log(f"Heartbeat error -> {e}", Logger.ERROR)
                await self._handle_disconnection(err=e)
                break

    async def _handle_disconnection(self, err: Optional[Exception] = None):
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
        try:
            await self._invoke_callback(
                callback=self.disconnected_callback, client=self._client_obj
            )
        except CallbackError as e:
            self._logger.log(f"Error in disconnected callback -> {e}", Logger.ERROR)

        self._logger.warning(f"Client disconnected from server ({err})")

    async def _close_all_streams(self):
        """Close all stream connections"""
        try:
            # Get current client
            if self._client_obj is not None:
                conn = self._client_obj.get_connection()
                if conn is not None:
                    await conn.wait_closed()

        except Exception as e:
            self._logger.warning(f"Error closing streams -> {e}")

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
