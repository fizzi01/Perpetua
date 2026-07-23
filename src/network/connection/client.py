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

import time

import asyncio
import ssl
from typing import Optional, Callable, Any

from model.client import ClientsManager, ClientObj
from model.connection import StreamWrapper, ClientConnection

from network.data.exchange import MessageExchange, MessageExchangeConfig
from network.protocol.message import MessageType, ProtocolMessage
from network.stream import StreamType

from utils.logging import Logger, get_logger
from utils import ExponentialBackoff
from utils.net import set_socket_nodelay

from .handler import (
    CallbackError,
    BaseConnectionHandler,
    apply_skew_tolerant_time_policy,
    peer_cert_is_expired,
)


class StaleCertificateError(Exception):
    """Raised when the locally stored CA certificate fails TLS verification
    against the server. Signals that the cert needs to be deleted and a new
    pairing flow must run before connections can resume.
    """

    pass


def _is_hostname_mismatch(error: ssl.SSLCertVerificationError) -> bool:
    """True when the TLS failure is a hostname/IP-SAN mismatch, not distrust.

    A mismatch means the CA still trusts the presented chain but the leaf's
    Subject Alternative Name does not cover the address we dialed (typically
    the server changed IP and hasn't re-issued its leaf yet). This is NOT a
    stale CA and must NOT trigger a cert purge / OTP re-pair. OpenSSL reports
    it with verify_code 62 ("Hostname mismatch"); the ``verify_message`` reads
    "IP address mismatch" or "Hostname mismatch" depending on the SAN type.
    """
    message = (getattr(error, "verify_message", "") or "").lower()
    if "mismatch" in message:
        return True
    # Fall back to the verify code (62 == X509_V_ERR_HOSTNAME_MISMATCH) for
    # builds where verify_message is empty.
    return getattr(error, "verify_code", None) == 62


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
    MAX_HEARTBEAT_MISSES = 2

    BACKOFF_INITIAL_DELAY = 1.0  # Start with 1 second
    BACKOFF_MAX_DELAY = 60.0  # Cap at 1 minute
    BACKOFF_MULTIPLIER = 2.0  # Double each time
    BACKOFF_ERROR_THRESHOLD = 5  # Errors before backoff

    def __init__(
        self,
        connected_callback: Optional[Callable[["ClientObj"], Any]] = None,
        disconnected_callback: Optional[Callable[["ClientObj"], Any]] = None,
        reconnected_callback: Optional[Callable[["ClientObj", list[int]], Any]] = None,
        connecting_callback: Optional[Callable[["ClientObj"], Any]] = None,
        stale_cert_callback: Optional[Callable[[], Any]] = None,
        server_uid_callback: Optional[Callable[[str], Any]] = None,
        host: str = "127.0.0.1",
        port: int = 5001,
        wait: int = 5,
        heartbeat_interval: int = 10,
        max_errors: int = 10,
        clients: Optional[ClientsManager] = None,
        open_streams: Optional[list[int]] = None,
        certfile: Optional[str] = None,
        client_certfile: Optional[str] = None,
        client_keyfile: Optional[str] = None,
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
        # Invoked when the loop enters a connecting phase (initial attempt or
        # the start of a reconnect streak), at most once per phase - the Client
        # service turns this into a "connecting" status notification for the
        # GUI. Not fired on terminal paths (stale cert) since the loop breaks.
        self.connecting_callback = connecting_callback
        # Invoked at most once per ConnectionHandler instance when TLS
        # verification fails against the server (stale local CA). The Client
        # service uses this to delete the cached cert and re-trigger the
        # OTP pairing flow so the user isn't left staring at a cryptic
        # SSL error in the log.
        self.stale_cert_callback = stale_cert_callback
        # Invoked with the server's UID once it arrives in the handshake
        # ack. The Client service uses this to persist the UID locally so
        # the certificate mapping has a stable, non-empty key even when
        # mDNS discovery wasn't the entry point.
        self.server_uid_callback = server_uid_callback

        self.host = host
        self.port = port
        self.wait = wait
        self.max_errors = max_errors
        self.heartbeat_interval = heartbeat_interval
        self.auto_reconnect = auto_reconnect

        self.certfile = certfile
        # Client identity for mutual TLS: the CA-signed leaf cert and its
        # private key. Presented to the server so it can cryptographically
        # authenticate this client (CN == UID). The key never leaves here.
        self.client_certfile = client_certfile
        self.client_keyfile = client_keyfile
        self.use_ssl = use_ssl
        self._ssl_context_cache: Optional[tuple[Optional[tuple], ssl.SSLContext]] = None

        if self.use_ssl and self.certfile is None:
            raise ValueError("SSL is enabled but no certificate file provided")

        self.clients = (
            clients if clients is not None else ClientsManager(client_mode=True)
        )

        self.open_streams = open_streams if open_streams is not None else []

        # Connection state
        self._running = False
        self._connected = False

        self._backoff = ExponentialBackoff(
            initial_delay=self.BACKOFF_INITIAL_DELAY,
            max_delay=self.BACKOFF_MAX_DELAY,
            multiplier=self.BACKOFF_MULTIPLIER,
            jitter=False,
        )

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
            self._logger.error("Failed to start", error=str(e))
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

    def update_target(self, host: str, port: int) -> None:
        """Retarget the connection to a new host/port.

        ``host``/``port`` are read fresh at every ``_connect()`` attempt, so
        updating them here makes the next retry (initial connect or reconnect)
        aim at the new address. The Client service calls this when mDNS
        re-resolves the saved server to a different IP (DHCP renewal, interface
        switch), so the retry loop follows the server without a re-pairing.
        """
        if (host and host != self.host) or (port and port != self.port):
            self._logger.log(
                f"Connection target updated {self.host}:{self.port} -> {host}:{port}",
                Logger.INFO,
            )
            if host:
                self.host = host
            if port:
                self.port = port

    async def _core_loop(self):
        """Main connection loop with automatic reconnection"""
        error_count = 0
        # Fire ``connecting_callback`` once per connecting phase (not on every
        # loop tick / backoff sleep). Reset on a successful handshake so the
        # next disconnect->reconnect streak announces again.
        announced_connecting = False

        while self._running:
            try:
                if not self._connected:
                    self._logger.log(
                        f"Attempting to connect to {self.host}:{self.port}...",
                        Logger.INFO,
                    )

                    if not announced_connecting:
                        announced_connecting = True
                        if self.connecting_callback:
                            try:
                                await self._invoke_callback(
                                    callback=self.connecting_callback,
                                    client=self._client_obj,
                                )
                            except CallbackError as e:
                                self._logger.log(
                                    f"Error in connecting callback ({e})",
                                    Logger.ERROR,
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
                            self._backoff.reset()
                            # A new disconnect streak should re-announce.
                            announced_connecting = False

                            self._logger.log(
                                "Handshake successful, client connected", Logger.INFO
                            )

                            # Update client status
                            self._client_obj.set_connection_status(status=True)
                            if self._command_stream is None:
                                raise Exception(
                                    "Command stream is None after handshake"
                                )

                            sockname = self._command_stream.get_sockname()
                            if sockname:
                                self._client_obj.ip_address = sockname[0]
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
                                        f"Error in connected callback ({e})",
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
                        if 0 < self.max_errors <= error_count:
                            error_count = self.max_errors  # Cap error count
                            if self.auto_reconnect:
                                # Enter exponential backoff mode
                                delay = self._backoff.get_next_delay()
                                self._logger.log(
                                    f"Max connection errors reached ({error_count}/{self.max_errors}), "
                                    f"Retrying in {delay:.2f} seconds.",
                                    Logger.WARNING,
                                )
                                await asyncio.sleep(delay)
                            else:
                                raise Exception("Max connection errors reached")
                        else:
                            await asyncio.sleep(self.wait)
                        continue

                # Connection is established, just wait
                await asyncio.sleep(self.heartbeat_interval)

            except asyncio.CancelledError:
                self._logger.log("Core loop cancelled", Logger.DEBUG)
                self._connected = False
                await self.stop()
                break
            except StaleCertificateError as e:
                # Stop the loop, the cert can't verify and retrying would
                # just produce the same error. Hand control to the upper
                # layer via the callback so it can wipe the cert, surface a
                # notification, and (optionally) re-pair.
                self._logger.log(
                    f"Stale CA certificate detected ({e}); stopping reconnect loop.",
                    Logger.WARNING,
                )
                # Full disconnection path: closes streams, marks the client
                # as disconnected and - critically - fires
                # disconnected_callback so the upper layer transitions out
                # of the "connecting" UI state instead of staying pending.
                await self._handle_disconnection(err=e)
                if self.stale_cert_callback:
                    try:
                        result = self.stale_cert_callback()
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception as cb_err:
                        self._logger.log(
                            f"Error in stale_cert_callback ({cb_err})", Logger.ERROR
                        )
                self._running = False
                break
            except Exception as e:
                self._logger.exception("Error in core loop", error=str(e))

                # Handle disconnection
                if self._connected:
                    await self._handle_disconnection()

                error_count += 1
                if error_count >= self.max_errors and not self.auto_reconnect:
                    error_count = self.max_errors  # Cap error count
                    self._logger.log(
                        "Max errors reached and auto reconnect disabled, stopping",
                        Logger.ERROR,
                    )
                    self._running = False
                    break

                await asyncio.sleep(self.wait)

        self._connected = False

    async def _connect(self) -> bool:
        """Establish command stream connection"""
        try:
            # When TLS is enabled the connection is wrapped in mutual TLS from
            # the start, so the handshake and COMMAND channel are encrypted and
            # the server can authenticate us via our client certificate.
            ssl_context = self._get_ssl_context()

            # Connect to server
            if ssl_context is not None:
                open_coro = asyncio.open_connection(
                    self.host,
                    self.port,
                    ssl=ssl_context,
                    server_hostname=self.host,
                )
            else:
                open_coro = asyncio.open_connection(self.host, self.port)
            _command_reader, _command_writer = await asyncio.wait_for(
                open_coro,
                timeout=self.CONNECTION_ATTEMPT_TIMEOUT,
            )
            set_socket_nodelay(_command_writer)

            # The handshake tolerates clock skew (no notBefore check), but a
            # genuinely expired server cert must still be refused. All streams
            # reuse this server cert, so one check on the command connection
            # covers them. Surface it as a stale-cert error so the service
            # purges and re-pairs rather than looping.
            if ssl_context is not None and peer_cert_is_expired(
                _command_writer.get_extra_info("ssl_object")
            ):
                self._logger.log(
                    f"Server certificate from {self.host}:{self.port} has "
                    f"expired; re-pairing required.",
                    Logger.ERROR,
                )
                _command_writer.close()
                raise StaleCertificateError("server certificate expired")

            self._command_stream = StreamWrapper(
                reader=_command_reader, writer=_command_writer
            )

            self._logger.debug(
                "Connected",
                host=self.host,
                port=self.port,
                tls=ssl_context is not None,
            )
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
        except ssl.SSLCertVerificationError as e:
            if _is_hostname_mismatch(e):
                # The CA still trusts the chain; only the leaf's SAN is out of
                # date (e.g. the server changed IP and hasn't re-issued its leaf
                # yet). Purging our CA and re-pairing would NOT fix this, so
                # treat it as a retryable connection failure and let the server
                # catch up while auto-reconnect keeps trying.
                self._logger.log(
                    f"TLS hostname/IP mismatch connecting to {self.host}:{self.port}: {e}. "
                    f"The server's certificate does not cover this address yet; "
                    f"retrying without re-pairing.",
                    Logger.WARNING,
                )
                return False
            # Genuine CA-trust failure: the server regenerated its CA but we
            # still hold an old one. Surface a specific error so the service can
            # purge and re-pair instead of looping on a cryptic SSL log line.
            self._logger.log(
                f"TLS verification failed connecting to {self.host}:{self.port}: {e}. "
                f"The locally stored CA certificate looks stale.",
                Logger.ERROR,
            )
            raise StaleCertificateError(str(e)) from e
        except StaleCertificateError:
            # Expired-server-cert path above: propagate to the service's
            # purge/re-pair handler instead of being swallowed as a generic
            # connection error.
            raise
        except Exception as e:
            self._logger.error("Connection error", error=str(e))
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

            # Advertise per-monitor layout for server edge-routing. On
            # failure, server falls back to legacy screen_resolution.
            monitors_payload: list[dict] = []
            try:
                from utils.screen import Screen

                monitors_payload = [m.to_dict() for m in Screen.get_monitors()]
            except Exception as e:
                self._logger.log(
                    f"Failed to probe monitor list for handshake ({e}); "
                    "server will fall back to single-monitor mode",
                    Logger.DEBUG,
                )

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
                monitors=monitors_payload,
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

            # Server now advertises its UID in the ack so the client can
            # persist a stable identifier (used as the cert-mapping key)
            # without depending on mDNS discovery. Fire the callback once
            # per handshake; the upstream Client service decides whether
            # to write it to disk.
            server_uid = handshake_ack.payload.get("server_uid", "")
            if server_uid and self.server_uid_callback:
                try:
                    result = self.server_uid_callback(server_uid)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception as e:
                    self._logger.log(
                        f"Error in server_uid_callback ({e})", Logger.ERROR
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
        except StaleCertificateError:
            # Bubble up so the core loop can fire the stale-cert callback
            # and stop reconnecting against a cert that can never verify.
            raise
        except Exception as e:
            self._logger.error("Handshake error", error=str(e))
            import traceback

            self._logger.log(traceback.format_exc(), Logger.ERROR)
            return False

    def _get_ssl_context(self) -> Optional[ssl.SSLContext]:
        """
        Lazily build and cache the client SSL context.
        Re-uses the same context across all streams to avoid re-parsing the CA.
        """
        if not self.use_ssl or not self.certfile:
            return None
        cache_key = (self.certfile, self.client_certfile, self.client_keyfile)
        if (
            self._ssl_context_cache is not None
            and self._ssl_context_cache[0] == cache_key
        ):
            return self._ssl_context_cache[1]
        context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
        context.load_verify_locations(self.certfile)
        # Tolerate clock skew: a client whose clock trails the server must not
        # reject a valid server cert as "not yet valid". CA/chain/hostname
        # verification stays intact; expiry is re-checked post-handshake.
        apply_skew_tolerant_time_policy(context)
        # Present our client identity for mutual TLS. Absent it, a TLS-mode
        # server (verify_mode=CERT_REQUIRED) refuses the connection — which is
        # the signal to (re-)pair and obtain a client certificate.
        if self.client_certfile and self.client_keyfile:
            context.load_cert_chain(
                certfile=self.client_certfile, keyfile=self.client_keyfile
            )
        self._ssl_context_cache = (cache_key, context)
        return context

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
        ssl_context = self._get_ssl_context()

        for stream_type in streams:
            try:
                # Connect to server for this stream. When TLS is on the stream
                # is wrapped from the start (matching the server listener), so
                # there is no separate start_tls upgrade step.
                if ssl_context is not None:
                    open_coro = asyncio.open_connection(
                        self.host,
                        self.port,
                        ssl=ssl_context,
                        server_hostname=self.host,
                    )
                else:
                    open_coro = asyncio.open_connection(self.host, self.port)
                reader, writer = await asyncio.wait_for(
                    open_coro,
                    timeout=self.CONNECTION_ATTEMPT_TIMEOUT,
                )
                set_socket_nodelay(writer)

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

                self._logger.debug("Stream connected", stream_type=stream_type)

            except asyncio.TimeoutError:
                self._logger.log(
                    f"Timeout connecting stream {stream_type}", Logger.ERROR
                )
                return False
            except ssl.SSLCertVerificationError as e:
                if _is_hostname_mismatch(e):
                    # Leaf SAN out of date (e.g. server IP changed), CA still
                    # valid. Retryable — don't wipe the CA or force re-pairing.
                    self._logger.log(
                        f"TLS hostname/IP mismatch on stream {stream_type}: {e}. "
                        f"Retrying without re-pairing.",
                        Logger.WARNING,
                    )
                    return False
                # The local CA can't verify the server's cert. Almost always
                # means the server regenerated its CA but we still hold an
                # older one. Surface this as a specific error so the upper
                # layer can wipe the cert and re-pair instead of looping
                # forever on a cryptic SSL log line.
                self._logger.log(
                    f"TLS verification failed on stream {stream_type}: {e}. "
                    f"The locally stored CA certificate looks stale.",
                    Logger.ERROR,
                )
                raise StaleCertificateError(str(e)) from e
            except ssl.SSLError as e:
                # Some Python builds raise the generic SSLError instead of
                # the specific subclass for verification failures. Detect by
                # message and treat the same.
                msg = str(e).lower()
                if "mismatch" in msg:
                    # Leaf SAN out of date, CA still valid — retryable.
                    self._logger.log(
                        f"TLS hostname/IP mismatch on stream {stream_type}: {e}. "
                        f"Retrying without re-pairing.",
                        Logger.WARNING,
                    )
                    return False
                if (
                    "certificate verify failed" in msg
                    or "certificate signature failure" in msg
                ):
                    self._logger.log(
                        f"TLS verification failed on stream {stream_type}: {e}. "
                        f"The locally stored CA certificate looks stale.",
                        Logger.ERROR,
                    )
                    raise StaleCertificateError(str(e)) from e
                self._logger.log(
                    f"SSL error on stream {stream_type} ({e})", Logger.ERROR
                )
                return False
            except Exception as e:
                self._logger.log(
                    f"Failed to connect stream {stream_type} ({e})", Logger.ERROR
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
                        else:
                            # Send heartbeat
                            hb_msg = ProtocolMessage(
                                message_type=MessageType.HEARTBEAT,
                                source="server",
                                payload={},
                                timestamp=time.time(),
                                sequence_id=0,
                            )

                            try:
                                await stream_writer.send(hb_msg.to_bytes())
                            except Exception as e:
                                self._logger.warning(
                                    f"Heartbeat send failed on stream {stream_type} ({e})"
                                )
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
                                f"Error in reconnected callback ({e})", Logger.ERROR
                            )

            except asyncio.CancelledError:
                break
            except ConnectionResetError as e:
                if heartbeat_trials < self.MAX_HEARTBEAT_MISSES:
                    heartbeat_trials += 1
                    self._logger.log(
                        f"Heartbeat missed {heartbeat_trials}/{self.MAX_HEARTBEAT_MISSES} "
                        f"(exc_type={type(e).__name__}, exc={e!r})",
                        Logger.WARNING,
                    )
                    continue
                else:
                    self._logger.log(
                        f"Heartbeat detected disconnection "
                        f"(exc_type={type(e).__name__}, exc={e!r})",
                        Logger.WARNING,
                    )
                    await self._handle_disconnection(err=e)
                    break
            except Exception as e:
                self._logger.log(
                    f"Heartbeat error (exc_type={type(e).__name__}, exc={e!r})",
                    Logger.ERROR,
                )
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
            self._logger.error("Error in disconnected callback", error=str(e))

        self._logger.warning("Client disconnected from server", error=str(err))

    async def _close_all_streams(self):
        """Close all stream connections"""
        try:
            # Get current client
            if self._client_obj is not None:
                conn = self._client_obj.get_connection()
                if conn is not None:
                    await conn.wait_closed()

        except Exception as e:
            self._logger.warning("Error closing streams", error=str(e))

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
