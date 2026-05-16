"""
Secure certificate sharing system with OTP and JWT
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
import secrets
import time
import hashlib
import base64
from typing import Optional, Tuple, Callable, Awaitable, Dict
import jwt
from datetime import datetime, timedelta, timezone
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from utils.logging import Logger, get_logger

OTP_LENGTH = 6

# Wire protocol tags used over the plaintext pairing/cert channel.
REQ_REQUEST_PAIRING = "REQUEST_PAIRING"
REQ_GET_CERTIFICATE = "GET_CERTIFICATE"
RESP_OK = "OK"
RESP_TOKEN = "TOKEN"
RESP_ERROR = "ERROR"
ERR_NO_ACTIVE_OTP = "NO_ACTIVE_OTP"
ERR_OTP_EXPIRED = "OTP_EXPIRED"
ERR_RATE_LIMITED = "RATE_LIMITED"
ERR_UNKNOWN_REQUEST = "UNKNOWN_REQUEST"
# Returned when a pairing_request_callback raises (e.g. GUI not reachable).
# The fresh OTP generated for the request is invalidated server-side so it
# cannot be guessed in the 6-digit window.
ERR_CALLBACK_FAILED = "CALLBACK_FAILED"

# Read at most this many bytes before the first newline on a pre-auth connection,
# to avoid a hostile client tying up resources with a never-ending header.
_MAX_HEADER_BYTES = 1024
# Default per-IP cooldown between pairing requests (seconds).
DEFAULT_PAIRING_COOLDOWN = 5.0
# Default number of adjacent ports to try if the preferred one is occupied.
# Set to 0 to disable fallback and fail fast.
DEFAULT_PORT_FALLBACK_RANGE = 10


# Error class
class CertificateSharingError(Exception):
    """Custom exception for certificate sharing errors"""

    pass


class CertificateReceiveError(Exception):
    """Custom exception for certificate receiver errors"""

    pass


class CertificateSharing:
    """
    Manages secure certificate sharing using OTP and JWT.

    Opens a temporary server that accepts connections for certificate distribution.
    Uses OTP (One-Time Password) to encrypt JWT containing the certificate.
    """

    def __init__(
        self,
        cert_data: bytes,
        host: str = "0.0.0.0",
        port: int = 5556,
        timeout: int = 10,
        pairing_request_callback: Optional[
            Callable[[Dict[str, str]], Awaitable[None]]
        ] = None,
        pairing_cooldown: float = DEFAULT_PAIRING_COOLDOWN,
        port_fallback_range: int = DEFAULT_PORT_FALLBACK_RANGE,
    ):
        """
        Initialize certificate sharing manager.

        Args:
            cert_data: Certificate data to share
            host: Host address for temporary server
            port: Port for temporary server
            timeout: Default OTP validity window in seconds (default: 10)
            pairing_request_callback: Optional async callback invoked whenever a
                client sends ``REQUEST_PAIRING``. Receives a dict with keys
                ``peer_ip``, ``peer_port``, ``hostname`` (best-effort, may be
                empty), ``otp`` and ``timeout``. The callback is awaited before
                replying to the client.
            pairing_cooldown: Per-IP minimum seconds between pairing requests.
                Set to 0 to disable rate-limiting.
            port_fallback_range: How many adjacent ports to try if the
                preferred one is busy. Set to 0 to disable fallback and fail
                fast. The actually bound port is exposed via
                :meth:`get_actual_port`.
        """
        self._cert_data = cert_data
        self._host = host
        self._port = port
        self._actual_port: Optional[int] = None
        self._port_fallback_range = max(0, port_fallback_range)
        self._timeout = timeout

        self._otp: Optional[str] = None
        self._otp_expiry: Optional[float] = None
        self._server: Optional[asyncio.Server] = None
        self._running = False
        self._shared = False
        self._service_mode = False
        # OTP is regenerated on each request unless still valid. The lock keeps
        # concurrent pairing requests from racing each other.
        self._otp_lock = asyncio.Lock()
        self._auto_shutdown_task: Optional[asyncio.Task] = None

        self._pairing_request_callback = pairing_request_callback
        self._pairing_cooldown = max(0.0, pairing_cooldown)
        self._last_request_at: Dict[str, float] = {}

        self._logger = get_logger(self.__class__.__name__)

    def set_pairing_request_callback(
        self,
        callback: Optional[Callable[[Dict[str, str]], Awaitable[None]]],
    ) -> None:
        """Update the pairing request callback. Safe to call at any time."""
        self._pairing_request_callback = callback

    def update_cert_data(self, cert_data: bytes) -> None:
        """Replace the certificate payload (used when certs are regenerated)."""
        self._cert_data = cert_data

    def get_actual_port(self) -> Optional[int]:
        """Return the port the listener actually bound (post-fallback)."""
        return self._actual_port

    async def _bind_with_fallback(self) -> Optional[asyncio.Server]:
        """Try the preferred port, then walk forward through adjacent ports.

        Returns the asyncio.Server on success, or None if every candidate
        port in ``[port, port + port_fallback_range]`` was busy. Logs each
        attempt so the admin can see why we moved on.
        """
        last_err: Optional[OSError] = None
        for offset in range(self._port_fallback_range + 1):
            candidate = self._port + offset
            try:
                server = await asyncio.start_server(
                    self._handle_client, self._host, candidate
                )
                if offset > 0:
                    self._logger.log(
                        f"Preferred port {self._port} busy; "
                        f"using fallback port {candidate}",
                        Logger.WARNING,
                    )
                self._actual_port = candidate
                return server
            except OSError as e:
                # errno 48 on macOS, 98 on Linux, 10048 on Windows
                if (
                    e.errno in (48, 98, 10048)
                    or "address already in use" in str(e).lower()
                ):
                    last_err = e
                    self._logger.log(
                        f"Port {candidate} busy; trying next fallback",
                        Logger.DEBUG,
                    )
                    continue
                # Unrelated OS error: don't try further candidates.
                self._logger.log(
                    f"Failed to bind {self._host}:{candidate} ({e})",
                    Logger.ERROR,
                )
                raise
        if last_err is not None:
            self._logger.log(
                f"All {self._port_fallback_range + 1} candidate ports "
                f"({self._port}..{self._port + self._port_fallback_range}) "
                f"are occupied",
                Logger.ERROR,
            )
        return None

    @staticmethod
    def _generate_otp() -> str:
        """Generate a secure OTP_LENGHT-digit OTP"""
        return "".join([str(secrets.randbelow(10)) for _ in range(OTP_LENGTH)])

    @staticmethod
    def _derive_key_from_otp(otp: str, salt: bytes) -> bytes:
        """
        Derive AES-256 key from OTP using PBKDF2.

        Args:
            otp: One-time password
            salt: Random salt for key derivation

        Returns:
            32-byte key suitable for AES-256
        """
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
        )
        return kdf.derive(otp.encode("utf-8"))

    @staticmethod
    async def _derive_key_from_otp_async(otp: str, salt: bytes) -> bytes:
        # PBKDF2 is CPU-bound, keep it off the event loop.
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, CertificateSharing._derive_key_from_otp, otp, salt
        )

    @staticmethod
    def encrypt_data(data: bytes, otp: str) -> Tuple[bytes, bytes, bytes]:
        """
        Encrypt data using AES-GCM with key derived from OTP.

        Args:
            data: Data to encrypt
            otp: One-time password used to derive encryption key

        Returns:
            Tuple of (encrypted_data, nonce, salt)
        """
        # Generate random salt and nonce
        salt = secrets.token_bytes(16)
        nonce = secrets.token_bytes(12)  # GCM standard nonce size

        # Derive key from OTP
        key = CertificateSharing._derive_key_from_otp(otp, salt)

        # Encrypt data
        aesgcm = AESGCM(key)
        encrypted_data = aesgcm.encrypt(nonce, data, None)

        return encrypted_data, nonce, salt

    @staticmethod
    async def encrypt_data_async(data: bytes, otp: str) -> Tuple[bytes, bytes, bytes]:
        salt = secrets.token_bytes(16)
        nonce = secrets.token_bytes(12)
        key = await CertificateSharing._derive_key_from_otp_async(otp, salt)
        aesgcm = AESGCM(key)
        encrypted_data = aesgcm.encrypt(nonce, data, None)
        return encrypted_data, nonce, salt

    @staticmethod
    def decrypt_data(
        encrypted_data: bytes, nonce: bytes, salt: bytes, otp: str
    ) -> bytes:
        """
        Decrypt data using AES-GCM with key derived from OTP.

        Args:
            encrypted_data: Encrypted data
            nonce: Nonce used for encryption
            salt: Salt used for key derivation
            otp: One-time password used to derive decryption key

        Returns:
            Decrypted data
        """
        # Derive key from OTP
        key = CertificateSharing._derive_key_from_otp(otp, salt)

        # Decrypt data
        aesgcm = AESGCM(key)
        return aesgcm.decrypt(nonce, encrypted_data, None)

    @staticmethod
    async def decrypt_data_async(
        encrypted_data: bytes, nonce: bytes, salt: bytes, otp: str
    ) -> bytes:
        key = await CertificateSharing._derive_key_from_otp_async(otp, salt)
        aesgcm = AESGCM(key)
        return aesgcm.decrypt(nonce, encrypted_data, None)

    async def _create_jwt(self, otp: str) -> str:
        """
        Create JWT containing encrypted certificate data.

        Args:
            otp: One-time password used for encryption

        Returns:
            JWT token with encrypted certificate
        """
        # Encrypt certificate data (PBKDF2 runs in executor)
        encrypted_data, nonce, salt = await self.encrypt_data_async(
            self._cert_data, otp
        )

        # Encode binary data to base64 for JSON serialization
        payload = {
            "encrypted_cert": base64.b64encode(encrypted_data).decode("utf-8"),
            "nonce": base64.b64encode(nonce).decode("utf-8"),
            "salt": base64.b64encode(salt).decode("utf-8"),
            "exp": datetime.now(timezone.utc) + timedelta(seconds=self._timeout),
            "iat": datetime.now(tz=timezone.utc).timestamp(),
        }

        # Sign JWT (using a hash of OTP to avoid using OTP directly as JWT secret)
        jwt_secret = hashlib.sha256(otp.encode("utf-8")).hexdigest()
        return jwt.encode(payload, jwt_secret, algorithm="HS256")

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ):
        """
        Handle a client connection on the pairing/cert-sharing port.

        Protocol (plaintext, line-oriented, terminated by empty line):

        - ``REQUEST_PAIRING\\n[HOSTNAME:<name>\\n]\\n`` — ask the server to
          generate (or refresh) an OTP and surface it to the local admin via
          ``pairing_request_callback``. The server replies with
          ``OK:<remaining_seconds>\\n``. The OTP itself is never sent over the
          wire — it must reach the client out-of-band (e.g. read off the
          server's GUI by a human).

        - ``GET_CERTIFICATE\\n\\n`` — request the encrypted CA bundle. Replies
          with ``TOKEN:<jwt>\\n`` or ``ERROR:<code>\\n``.

        Legacy clients that immediately read without sending a request are
        still served (treated as ``GET_CERTIFICATE``) to keep one-shot
        ``start_sharing()`` flows working.
        """
        addr = writer.get_extra_info("peername")
        peer_ip = addr[0] if addr else "unknown"
        peer_port = addr[1] if addr and len(addr) > 1 else 0
        self._logger.log(f"Pairing client connected from {addr}", Logger.INFO)

        try:
            request_type, headers = await self._read_request(reader)

            if request_type == REQ_REQUEST_PAIRING:
                await self._handle_pairing_request(writer, peer_ip, peer_port, headers)
            elif request_type == REQ_GET_CERTIFICATE or request_type is None:
                await self._handle_certificate_request(writer, addr)
            else:
                self._logger.log(
                    f"Unknown request '{request_type}' from {addr}", Logger.WARNING
                )
                writer.write(f"{RESP_ERROR}:{ERR_UNKNOWN_REQUEST}\n".encode("utf-8"))
                await writer.drain()

        except Exception as e:
            self._logger.log(f"Error handling client {addr} ({e})", Logger.ERROR)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _read_request(
        self, reader: asyncio.StreamReader
    ) -> Tuple[Optional[str], Dict[str, str]]:
        """
        Read a request from a client connection.

        Returns a tuple of (request_type, headers). ``request_type`` is None
        when the client closes the connection without sending anything (legacy
        one-shot path).
        """
        try:
            first = await asyncio.wait_for(
                reader.readuntil(b"\n"), timeout=self._read_timeout()
            )
        except asyncio.IncompleteReadError:
            return None, {}
        except asyncio.TimeoutError:
            # Old clients connect-and-read; preserve legacy behaviour.
            return None, {}
        except asyncio.LimitOverrunError:
            return None, {}

        request_type = first.decode("utf-8", errors="replace").strip().upper()
        if not request_type:
            return None, {}

        headers: Dict[str, str] = {}
        # Read additional header lines until an empty line (request terminator).
        # Bail out if the cumulative size grows beyond _MAX_HEADER_BYTES.
        consumed = len(first)
        while True:
            try:
                line = await asyncio.wait_for(
                    reader.readuntil(b"\n"), timeout=self._read_timeout()
                )
            except (asyncio.IncompleteReadError, asyncio.TimeoutError):
                break
            consumed += len(line)
            if consumed > _MAX_HEADER_BYTES:
                break
            decoded = line.decode("utf-8", errors="replace").strip()
            if not decoded:
                break
            if ":" in decoded:
                key, _, value = decoded.partition(":")
                headers[key.strip().upper()] = value.strip()

        return request_type, headers

    def _read_timeout(self) -> float:
        """How long to wait for a request line before falling back to legacy.

        Short enough that legacy connect-and-read clients aren't held up; long
        enough that a real request from a slow network arrives.
        """
        return 1.0 if self._service_mode else 0.5

    async def _handle_pairing_request(
        self,
        writer: asyncio.StreamWriter,
        peer_ip: str,
        peer_port: int,
        headers: Dict[str, str],
    ) -> None:
        # Per-IP rate limit: protect the server admin from OTP-spam DoS.
        now = time.time()
        last = self._last_request_at.get(peer_ip, 0.0)
        if self._pairing_cooldown > 0 and (now - last) < self._pairing_cooldown:
            remaining = self._pairing_cooldown - (now - last)
            self._logger.log(
                f"Pairing request from {peer_ip} rate-limited "
                f"({remaining:.1f}s remaining)",
                Logger.WARNING,
            )
            writer.write(
                f"{RESP_ERROR}:{ERR_RATE_LIMITED}:{int(remaining) + 1}\n".encode(
                    "utf-8"
                )
            )
            await writer.drain()
            return
        self._last_request_at[peer_ip] = now

        async with self._otp_lock:
            otp_was_active = self._is_otp_valid()
            if not otp_was_active:
                self._otp = self._generate_otp()
                self._otp_expiry = time.time() + self._timeout
                self._shared = False
                self._logger.log(
                    f"Generated OTP {self._otp}, valid {self._timeout}s) "
                    f"on pairing request from {peer_ip}",
                    Logger.INFO,
                )
            otp = self._otp
            remaining = max(0, int((self._otp_expiry or 0) - time.time()))

        if self._pairing_request_callback is not None and otp is not None:
            try:
                await self._pairing_request_callback(
                    {
                        "peer_ip": peer_ip,
                        "peer_port": str(peer_port),
                        "hostname": headers.get("HOSTNAME", ""),
                        "otp": otp,
                        "timeout": str(remaining),
                        "was_active": "1" if otp_was_active else "0",
                    }
                )
            except Exception as e:
                # Surface the failure to the peer instead of silently letting
                # the client wait for an OTP that was never delivered to the
                # admin. Also invalidate the freshly-generated OTP so a brute
                # force in the 6-digit window can't backdoor in.
                self._logger.log(
                    f"pairing_request_callback failed for {peer_ip} ({e}); "
                    "denying request and invalidating OTP",
                    Logger.WARNING,
                )
                if not otp_was_active:
                    async with self._otp_lock:
                        self._otp = None
                        self._otp_expiry = None
                writer.write(f"{RESP_ERROR}:{ERR_CALLBACK_FAILED}\n".encode("utf-8"))
                await writer.drain()
                return

        writer.write(f"{RESP_OK}:{remaining}\n".encode("utf-8"))
        await writer.drain()

    async def _handle_certificate_request(
        self, writer: asyncio.StreamWriter, addr
    ) -> None:
        if not self._is_otp_valid():
            code = ERR_OTP_EXPIRED if self._otp else ERR_NO_ACTIVE_OTP
            self._logger.log(
                f"Certificate request from {addr} rejected: {code}", Logger.WARNING
            )
            writer.write(f"{RESP_ERROR}:{code}\n".encode("utf-8"))
            await writer.drain()
            return

        # PBKDF2 derivation runs off the event loop inside _create_jwt.
        token = await self._create_jwt(self._otp)  # ty:ignore[invalid-argument-type]
        writer.write(f"{RESP_TOKEN}:{token}\n".encode("utf-8"))
        await writer.drain()

        self._shared = True
        self._logger.log(f"Certificate sent to client {addr}", Logger.INFO)

    def _is_otp_valid(self) -> bool:
        """Check if OTP is still valid"""
        if not self._otp or not self._otp_expiry:
            return False
        return time.time() < self._otp_expiry

    async def start_sharing(self) -> Tuple[bool, Optional[str]]:
        """
        Start the temporary server and generate an OTP immediately.

        Use this for one-shot flows where the admin manually triggers a share
        and expects the OTP to be available right away. The server auto-shuts
        down after ``timeout`` seconds.

        Returns:
            Tuple of (success, otp). OTP is None if failed.
        """
        if self._running:
            self._logger.log("Sharing already in progress", Logger.WARNING)
            return False, None

        try:
            self._otp = self._generate_otp()
            self._otp_expiry = time.time() + self._timeout
            self._shared = False

            server = await self._bind_with_fallback()
            if server is None:
                self._otp = None
                self._otp_expiry = None
                return False, None
            self._server = server

            self._running = True
            self._service_mode = False
            self._logger.log(
                f"Certificate sharing server started on "
                f"{self._host}:{self._actual_port}",
                Logger.INFO,
            )
            self._logger.log(
                f"OTP generated (len={len(self._otp)}, valid for {self._timeout}s)",
                Logger.INFO,
            )

            self._auto_shutdown_task = asyncio.create_task(self._auto_shutdown())

            return True, self._otp

        except Exception as e:
            self._logger.log(f"Failed to start sharing server ({e})", Logger.ERROR)
            self._otp = None
            self._otp_expiry = None
            return False, None

    async def start_service(self) -> bool:
        """
        Start the pairing/cert-sharing listener in always-on mode.

        Unlike :meth:`start_sharing`, no OTP is generated up-front: the OTP is
        created lazily when a client sends ``REQUEST_PAIRING`` or the admin
        invokes :meth:`ensure_active_otp`. The listener stays up until
        :meth:`stop_sharing` is called.
        """
        if self._running:
            self._logger.log(
                "Sharing service already running, leaving as-is", Logger.DEBUG
            )
            return True

        try:
            server = await self._bind_with_fallback()
            if server is None:
                return False
            self._server = server
            self._running = True
            self._service_mode = True
            self._shared = False
            self._otp = None
            self._otp_expiry = None
            self._logger.log(
                f"Pairing service listening on {self._host}:{self._actual_port}",
                Logger.INFO,
            )
            return True
        except Exception as e:
            self._logger.log(f"Failed to start pairing service ({e})", Logger.ERROR)
            return False

    async def ensure_active_otp(
        self, timeout: Optional[int] = None
    ) -> Tuple[Optional[str], int]:
        """
        Make sure an OTP is currently valid, generating one if needed.

        Args:
            timeout: Override the default validity window for the newly
                generated OTP. Ignored if an OTP is already active.

        Returns:
            Tuple of (otp, remaining_seconds). ``otp`` is None if the service
            isn't running.
        """
        if not self._running:
            return None, 0

        async with self._otp_lock:
            if self._is_otp_valid():
                remaining = int((self._otp_expiry or 0) - time.time())  # ty:ignore[unsupported-operator]
                return self._otp, max(0, remaining)

            ttl = timeout if timeout and timeout > 0 else self._timeout
            self._otp = self._generate_otp()
            self._otp_expiry = time.time() + ttl
            self._shared = False
            self._logger.log(
                f"Generated OTP (len={len(self._otp)}, valid {ttl}s) "
                "via ensure_active_otp",
                Logger.INFO,
            )
            return self._otp, ttl

    async def _auto_shutdown(self):
        """Automatically shutdown the one-shot server after timeout."""
        try:
            await asyncio.sleep(self._timeout)
        except asyncio.CancelledError:
            return
        if self._running and not self._service_mode:
            self._logger.log(
                "Timeout reached, shutting down sharing server", Logger.INFO
            )
            await self.stop_sharing()

    async def stop_sharing(self):
        """Stop server and invalidate OTP."""
        if not self._running:
            return

        self._running = False
        self._service_mode = False

        if self._auto_shutdown_task and not self._auto_shutdown_task.done():
            self._auto_shutdown_task.cancel()
        self._auto_shutdown_task = None

        if self._server:
            self._server.close()
            try:
                await self._server.wait_closed()
            except Exception:
                pass

        self._otp = None
        self._otp_expiry = None

        self._logger.log("Certificate sharing server stopped", Logger.INFO)

    def get_otp(self) -> Optional[str]:
        """
        Get current OTP if valid.

        Returns:
            OTP string if valid, None otherwise
        """
        if self._is_otp_valid():
            remaining = int(self._otp_expiry - time.time())  # ty:ignore[unsupported-operator]
            self._logger.log(f"OTP valid for {remaining}s more", Logger.DEBUG)
            return self._otp
        return None

    def is_sharing_active(self) -> bool:
        """Check if sharing server is running"""
        return self._running

    def was_shared(self) -> bool:
        """Check if certificate was successfully shared"""
        return self._shared


class CertificateReceiver:
    """
    Client-side handler for receiving certificates with OTP verification.
    """

    def __init__(self, server_host: str, server_port: int = 5556, timeout: int = 10):
        """
        Initialize certificate receiver.

        Args:
            server_host: Server host address
            server_port: Server port
            timeout: Connection timeout in seconds
        """
        self._server_host = server_host
        self._server_port = server_port
        self._timeout = timeout

        self._resolved_host: Optional[str] = None

        self.__writer: Optional[asyncio.StreamWriter] = None

        self._logger = get_logger(self.__class__.__name__)

    def get_resolved_host(self) -> Optional[str]:
        """
        Get resolved server host after connection.

        Returns:
            Resolved host string or None if not connected
        """
        return self._resolved_host

    async def request_pairing(
        self, hostname: Optional[str] = None
    ) -> Tuple[bool, int, Optional[str]]:
        """
        Signal the server that this client wants to pair.

        The server will auto-generate an OTP and surface it to its local admin
        (e.g. on the server's GUI). The OTP itself is **not** returned over the
        wire — it must be communicated out-of-band to the human running this
        client.

        Args:
            hostname: Optional friendly name for this client, shown on the
                server side to help the admin identify the request.

        Returns:
            Tuple of (success, otp_validity_seconds, error_code).
            ``otp_validity_seconds`` is meaningful only on success; it tells
            the caller how long the user has to enter the OTP.
            ``error_code`` is set on failure. Possible values include
            ``RATE_LIMITED:<seconds>``, ``CALLBACK_FAILED`` (server admin GUI
            could not be reached so the request was denied), ``TIMEOUT`` and
            ``CONNECTION_REFUSED``.
        """
        try:
            self._logger.log(
                f"Requesting pairing from {self._server_host}:{self._server_port}",
                Logger.INFO,
            )
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self._server_host, self._server_port),
                timeout=self._timeout,
            )

            try:
                sock = writer.get_extra_info("socket")
                if sock:
                    try:
                        self._resolved_host = sock.getpeername()[0]
                    except OSError as e:
                        # Half-closed sockets can fail here; keep going with
                        # _resolved_host=None and let the caller fall back to
                        # the configured host.
                        self._logger.log(
                            f"getpeername failed during pairing request ({e})",
                            Logger.DEBUG,
                        )

                request = f"{REQ_REQUEST_PAIRING}\n"
                if hostname:
                    request += f"HOSTNAME:{hostname}\n"
                request += "\n"
                writer.write(request.encode("utf-8"))
                await writer.drain()

                response = await asyncio.wait_for(
                    reader.readuntil(b"\n"), timeout=self._timeout
                )
                response = response.decode("utf-8").strip()
            finally:
                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception:
                    pass

            if response.startswith(f"{RESP_OK}:"):
                try:
                    remaining = int(response.split(":", 1)[1])
                except ValueError:
                    remaining = 0
                self._logger.log(
                    f"Pairing request accepted (OTP valid {remaining}s)", Logger.INFO
                )
                return True, remaining, None

            if response.startswith(f"{RESP_ERROR}:"):
                code = response.split(":", 1)[1]
                self._logger.log(f"Pairing request rejected ({code})", Logger.WARNING)
                return False, 0, code

            self._logger.log(
                f"Unexpected response to pairing request: {response!r}",
                Logger.WARNING,
            )
            return False, 0, "UNEXPECTED_RESPONSE"

        except asyncio.TimeoutError:
            self._logger.log("Pairing request timeout", Logger.WARNING)
            return False, 0, "TIMEOUT"
        except ConnectionRefusedError:
            self._logger.log(
                f"Pairing request refused by {self._server_host}:{self._server_port}",
                Logger.WARNING,
            )
            return False, 0, "CONNECTION_REFUSED"
        except Exception as e:
            self._logger.log(f"Pairing request failed: {e}", Logger.ERROR)
            return False, 0, "ERROR"

    async def receive_certificate(self, otp: str) -> Tuple[bool, Optional[str]]:
        """
        Connect to server and receive certificate using OTP.

        Args:
            otp: One-time password from server

        Returns:
            Tuple of (success, certificate_data). Certificate is None if failed.
        """
        try:
            self._logger.log(
                f"Connecting to {self._server_host}:{self._server_port}...",
                Logger.INFO,
                otp=otp,
            )

            # Connect to server (no SSL for this temporary connection)
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self._server_host, self._server_port),
                timeout=self._timeout,
            )
            self.__writer = writer

            # Get resolved host. ``getpeername`` can raise OSError on a
            # half-closed socket; treat the resolved host as unknown rather
            # than letting the failure bubble up and mask the real cert
            # receive error path.
            sock = writer.get_extra_info("socket")
            if sock:
                try:
                    self._resolved_host = sock.getpeername()[0]
                    self._logger.log(
                        f"Resolved server host: {self._resolved_host}", Logger.DEBUG
                    )
                except OSError as e:
                    self._logger.log(
                        f"getpeername failed during cert receive ({e})",
                        Logger.DEBUG,
                    )

            # Explicit request keeps the protocol unambiguous on the new
            # always-on listener. Legacy servers that connect-and-send still
            # work because they ignore inbound data.
            writer.write(f"{REQ_GET_CERTIFICATE}\n\n".encode("utf-8"))
            try:
                await writer.drain()
            except Exception:
                pass

            self._logger.log("Connected, waiting for certificate...", Logger.DEBUG)

            # Read response
            response = await asyncio.wait_for(
                reader.readuntil(b"\n"), timeout=self._timeout
            )
            response = response.decode("utf-8").strip()

            if response.startswith("ERROR:"):
                error_type = response.split(":", 1)[1]
                self._logger.log(f"Server error: {error_type}", Logger.ERROR)
                return False, None

            if not response.startswith("TOKEN:"):
                self._logger.log("Invalid server response", Logger.ERROR)
                return False, None

            # Extract JWT token
            token = response.split(":", 1)[1]

            # Verify and decode JWT using OTP hash as secret
            try:
                jwt_secret = hashlib.sha256(otp.encode("utf-8")).hexdigest()
                payload = jwt.decode(
                    token,
                    jwt_secret,
                    algorithms=["HS256"],
                    options={"verify_iat": False},
                )

                # Extract encrypted data components
                encrypted_cert_b64 = payload["encrypted_cert"]
                nonce_b64 = payload["nonce"]
                salt_b64 = payload["salt"]

                # Decode from base64
                encrypted_cert = base64.b64decode(encrypted_cert_b64)
                nonce = base64.b64decode(nonce_b64)
                salt = base64.b64decode(salt_b64)

                # Decrypt certificate data using OTP (PBKDF2 in executor)
                cert_data = await CertificateSharing.decrypt_data_async(
                    encrypted_cert, nonce, salt, otp
                )

                # Convert bytes to string if needed
                cert_data_str = (
                    cert_data.decode("utf-8")
                    if isinstance(cert_data, bytes)
                    else cert_data
                )

                self._logger.log(
                    "Certificate received and decrypted successfully", Logger.INFO
                )
                return True, cert_data_str

            except jwt.ExpiredSignatureError:
                self._logger.log("JWT expired", Logger.ERROR)
                return False, None
            except jwt.InvalidTokenError as e:
                self._logger.log(f"Invalid JWT or OTP: {e}", Logger.ERROR)
                return False, None
            except Exception as e:
                self._logger.log(
                    f"Failed to decrypt certificate data: {e}", Logger.ERROR
                )
                import traceback

                self._logger.log(traceback.format_exc(), Logger.DEBUG)
                return False, None

        except asyncio.TimeoutError:
            self._logger.log("Connection timeout", Logger.ERROR)
            return False, None
        except ConnectionRefusedError:
            self._logger.log(
                f"Connection refused by {self._server_host}:{self._server_port}",
                Logger.ERROR,
            )
            return False, None
        except Exception as e:
            self._logger.log(f"Error receiving certificate ({e})", Logger.ERROR)
            import traceback

            self._logger.log(traceback.format_exc(), Logger.ERROR)
            return False, None
        finally:
            if self.__writer:
                self.__writer.close()
                await self.__writer.wait_closed()
