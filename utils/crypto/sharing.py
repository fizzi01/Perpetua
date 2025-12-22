"""
Secure certificate sharing system with OTP and JWT
"""

import asyncio
import secrets
import time
import hashlib
import base64
from typing import Optional, Tuple
import jwt
from datetime import datetime, timedelta, timezone
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from utils.logging import Logger, get_logger


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
    ):
        """
        Initialize certificate sharing manager.

        Args:
            cert_data: Certificate data to share
            host: Host address for temporary server
            port: Port for temporary server
            timeout: Maximum time window in seconds (default: 10)
        """
        self._cert_data = cert_data
        self._host = host
        self._port = port
        self._timeout = timeout

        self._otp: Optional[str] = None
        self._otp_expiry: Optional[float] = None
        self._server: Optional[asyncio.Server] = None
        self._running = False
        self._shared = False

        self._logger = get_logger(self.__class__.__name__)

    @staticmethod
    def _generate_otp() -> str:
        """Generate a secure 6-digit OTP"""
        return "".join([str(secrets.randbelow(10)) for _ in range(6)])

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
        return kdf.derive(otp.encode('utf-8'))

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
    def decrypt_data(encrypted_data: bytes, nonce: bytes, salt: bytes, otp: str) -> bytes:
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

    def _create_jwt(self, otp: str) -> str:
        """
        Create JWT containing encrypted certificate data.

        Args:
            otp: One-time password used for encryption

        Returns:
            JWT token with encrypted certificate
        """
        # Encrypt certificate data
        encrypted_data, nonce, salt = self.encrypt_data(self._cert_data, otp)

        # Encode binary data to base64 for JSON serialization
        payload = {
            "encrypted_cert": base64.b64encode(encrypted_data).decode('utf-8'),
            "nonce": base64.b64encode(nonce).decode('utf-8'),
            "salt": base64.b64encode(salt).decode('utf-8'),
            "exp": datetime.now(timezone.utc) + timedelta(seconds=self._timeout),
            "iat": datetime.now(timezone.utc),
        }

        # Sign JWT (using a hash of OTP to avoid using OTP directly as JWT secret)
        jwt_secret = hashlib.sha256(otp.encode('utf-8')).hexdigest()
        return jwt.encode(payload, jwt_secret, algorithm="HS256")

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ):
        """
        Handle client connection and send encrypted certificate.

        Args:
            reader: Client stream reader
            writer: Client stream writer
        """
        addr = writer.get_extra_info("peername")
        self._logger.log(f"Client connected from {addr}", Logger.INFO)

        try:
            # Check if OTP is still valid
            if not self._is_otp_valid():
                self._logger.log(
                    f"OTP expired, rejecting client {addr}", Logger.WARNING
                )
                writer.write(b"ERROR:OTP_EXPIRED\n")
                await writer.drain()
                return

            # Create and send JWT
            token = self._create_jwt(self._otp)  # ty:ignore[invalid-argument-type]
            writer.write(f"TOKEN:{token}\n".encode("utf-8"))
            await writer.drain()

            self._shared = True
            self._logger.log(f"Certificate sent to client {addr}", Logger.INFO)

        except Exception as e:
            self._logger.log(f"Error handling client {addr} -> {e}", Logger.ERROR)
        finally:
            writer.close()
            await writer.wait_closed()

    def _is_otp_valid(self) -> bool:
        """Check if OTP is still valid"""
        if not self._otp or not self._otp_expiry:
            return False
        return time.time() < self._otp_expiry

    async def start_sharing(self) -> Tuple[bool, Optional[str]]:
        """
        Start temporary server and generate OTP.

        Returns:
            Tuple of (success, otp). OTP is None if failed.
        """
        if self._running:
            self._logger.log("Sharing already in progress", Logger.WARNING)
            return False, None

        try:
            # Generate OTP
            self._otp = self._generate_otp()
            self._otp_expiry = time.time() + self._timeout
            self._shared = False

            # Start temporary server
            self._server = await asyncio.start_server(
                self._handle_client, self._host, self._port
            )

            self._running = True
            self._logger.log(
                f"Certificate sharing server started on {self._host}:{self._port}",
                Logger.INFO,
            )
            self._logger.log(
                f"OTP: {self._otp} (valid for {self._timeout}s)", Logger.INFO
            )

            # Schedule automatic shutdown after timeout
            asyncio.create_task(self._auto_shutdown())

            return True, self._otp

        except Exception as e:
            self._logger.log(f"Failed to start sharing server -> {e}", Logger.ERROR)
            self._otp = None
            self._otp_expiry = None
            return False, None

    async def _auto_shutdown(self):
        """Automatically shutdown server after timeout"""
        await asyncio.sleep(self._timeout)
        if self._running:
            self._logger.log(
                "Timeout reached, shutting down sharing server", Logger.INFO
            )
            await self.stop_sharing()

    async def stop_sharing(self):
        """Stop temporary server and invalidate OTP"""
        if not self._running:
            return

        self._running = False

        if self._server:
            self._server.close()
            await self._server.wait_closed()

        # Invalidate OTP
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
                f"Connecting to {self._server_host}:{self._server_port}...", Logger.INFO, otp=otp
            )

            # Connect to server (no SSL for this temporary connection)
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self._server_host, self._server_port),
                timeout=self._timeout,
            )
            self.__writer = writer

            # Get resolved host
            sock = writer.get_extra_info("socket")
            if sock:
                self._resolved_host = sock.getpeername()[0]
                self._logger.log(
                    f"Resolved server host: {self._resolved_host}", Logger.DEBUG
                )

            self._logger.log("Connected, waiting for certificate...", Logger.DEBUG)

            # Read response
            response = await asyncio.wait_for(reader.readline(), timeout=self._timeout)
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
                jwt_secret = hashlib.sha256(otp.encode('utf-8')).hexdigest()
                payload = jwt.decode(token, jwt_secret, algorithms=["HS256"])

                # Extract encrypted data components
                encrypted_cert_b64 = payload["encrypted_cert"]
                nonce_b64 = payload["nonce"]
                salt_b64 = payload["salt"]

                # Decode from base64
                encrypted_cert = base64.b64decode(encrypted_cert_b64)
                nonce = base64.b64decode(nonce_b64)
                salt = base64.b64decode(salt_b64)

                # Decrypt certificate data using OTP
                cert_data = CertificateSharing.decrypt_data(encrypted_cert, nonce, salt, otp)

                # Convert bytes to string if needed
                cert_data_str = cert_data.decode('utf-8') if isinstance(cert_data, bytes) else cert_data

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
                self._logger.log(f"Failed to decrypt certificate data: {e}", Logger.ERROR)
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
            self._logger.log(f"Error receiving certificate -> {e}", Logger.ERROR)
            import traceback

            self._logger.log(traceback.format_exc(), Logger.ERROR)
            return False, None
        finally:
            if self.__writer:
                self.__writer.close()
                await self.__writer.wait_closed()
