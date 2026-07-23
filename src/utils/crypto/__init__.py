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

from cryptography.x509 import DNSName, IPAddress
from pathlib import Path
import msgspec.json
from typing import Tuple, Optional, Dict
from cryptography import x509
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.backends import default_backend
import datetime
import ipaddress

from config import ApplicationConfig
from utils.fs import atomic_write_bytes
from utils.logging import Logger, get_logger

_encoder = msgspec.json.Encoder()
_decoder = msgspec.json.Decoder()


class CertificateManager:
    """Manages the generation and distribution of TLS certificates for LAN"""

    def __init__(self, cert_dir: str | Path = "./certs"):
        self.cert_dir = Path(cert_dir)
        self.cert_dir.mkdir(exist_ok=True, parents=True)

        self.ca_cert_path = self.cert_dir / "ca.crt"
        self.ca_key_path = self.cert_dir / "ca.key"
        self.server_cert_path = self.cert_dir / "server.crt"
        self.server_key_path = self.cert_dir / "server.key"
        # Client-side identity material (mutual TLS). The private key never
        # leaves the client; the cert is the CA-signed leaf received at pairing.
        self.client_cert_path = self.cert_dir / "client.crt"
        self.client_key_path = self.cert_dir / "client.key"

        self._logger = get_logger(self.__class__.__name__)

    def generate_ca(self, force: bool = False) -> bool:
        """Generate CA certificate if it doesn't exist"""
        if self.ca_cert_path.exists() and not force:
            return True

        try:
            # Generate CA private key
            ca_key = rsa.generate_private_key(
                public_exponent=65537, key_size=4096, backend=default_backend()
            )

            # Create CA certificate
            subject = issuer = x509.Name(
                [
                    x509.NameAttribute(NameOID.COUNTRY_NAME, "IT"),
                    x509.NameAttribute(
                        NameOID.ORGANIZATION_NAME, ApplicationConfig.service_name
                    ),
                    x509.NameAttribute(
                        NameOID.COMMON_NAME, f"{ApplicationConfig.service_name} CA"
                    ),
                ]
            )

            ca_cert = (
                x509.CertificateBuilder()
                .subject_name(subject)
                .issuer_name(issuer)
                .public_key(ca_key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(datetime.datetime.now(datetime.UTC))
                .not_valid_after(
                    datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=3650)
                )
                .add_extension(
                    x509.BasicConstraints(ca=True, path_length=None),
                    critical=True,
                )
                .sign(ca_key, hashes.SHA256(), default_backend())
            )

            # Save CA key and certificate atomically. The private key is
            # written with mode 0o600 so it never appears with wider perms.
            atomic_write_bytes(
                self.ca_key_path,
                ca_key.private_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PrivateFormat.PKCS8,
                    encryption_algorithm=serialization.NoEncryption(),
                ),
                mode=0o600,
            )

            atomic_write_bytes(
                self.ca_cert_path,
                ca_cert.public_bytes(serialization.Encoding.PEM),
                mode=0o644,
            )

            return True
        except Exception as e:
            self._logger.error("CA generation error", error=str(e))
            return False

    def generate_server_certificate(
        self, hostname: str, ip_addresses: list[str], force: bool = False
    ) -> bool:
        """Generate server certificate signed by CA"""
        if self.server_cert_path.exists() and not force:
            return True

        try:
            # Load CA
            with open(self.ca_key_path, "rb") as f:
                ca_key = serialization.load_pem_private_key(
                    f.read(), password=None, backend=default_backend()
                )

            with open(self.ca_cert_path, "rb") as f:
                ca_cert = x509.load_pem_x509_certificate(f.read(), default_backend())

            # Generate server private key
            server_key = rsa.generate_private_key(
                public_exponent=65537, key_size=2048, backend=default_backend()
            )

            # Create Subject Alternative Names (SAN)
            san_list: list[DNSName | IPAddress] = [x509.DNSName(hostname)]
            for ip in ip_addresses:
                try:
                    san_list.append(x509.IPAddress(ipaddress.ip_address(ip)))
                except ValueError:
                    san_list.append(x509.DNSName(ip))

            # Create server certificate
            subject = x509.Name(
                [
                    x509.NameAttribute(NameOID.COUNTRY_NAME, "IT"),
                    x509.NameAttribute(
                        NameOID.ORGANIZATION_NAME, ApplicationConfig.service_name
                    ),
                    x509.NameAttribute(NameOID.COMMON_NAME, hostname),
                ]
            )

            server_cert = (
                x509.CertificateBuilder()
                .subject_name(subject)
                .issuer_name(ca_cert.subject)
                .public_key(server_key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(datetime.datetime.now(datetime.UTC))
                .not_valid_after(
                    datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=365)
                )
                .add_extension(
                    x509.SubjectAlternativeName(san_list),
                    critical=False,
                )
                .add_extension(
                    x509.BasicConstraints(ca=False, path_length=None),
                    critical=True,
                )
                .sign(ca_key, hashes.SHA256(), default_backend())  # ty:ignore[invalid-argument-type]
            )

            # Save server key and certificate atomically.
            atomic_write_bytes(
                self.server_key_path,
                server_key.private_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PrivateFormat.PKCS8,
                    encryption_algorithm=serialization.NoEncryption(),
                ),
                mode=0o600,
            )

            atomic_write_bytes(
                self.server_cert_path,
                server_cert.public_bytes(serialization.Encoding.PEM),
                mode=0o644,
            )

            return True
        except Exception as e:
            self._logger.error("Server certificate generation error", error=str(e))
            return False

    def generate_client_key_and_csr(self, uid: str) -> Optional[bytes]:
        """Generate a client private key and a CSR for mutual-TLS identity.

        The private key is persisted locally (``client.key``, mode 0o600) and
        never leaves this machine. Returns the CSR (PEM) to send to the server
        for signing, or None on failure. ``uid`` is placed in the CSR subject
        Common Name; the server re-asserts it when signing.
        """
        try:
            client_key = rsa.generate_private_key(
                public_exponent=65537, key_size=2048, backend=default_backend()
            )

            csr = (
                x509.CertificateSigningRequestBuilder()
                .subject_name(
                    x509.Name(
                        [
                            x509.NameAttribute(NameOID.COUNTRY_NAME, "IT"),
                            x509.NameAttribute(
                                NameOID.ORGANIZATION_NAME,
                                ApplicationConfig.service_name,
                            ),
                            x509.NameAttribute(NameOID.COMMON_NAME, uid),
                        ]
                    )
                )
                .sign(client_key, hashes.SHA256(), default_backend())
            )

            atomic_write_bytes(
                self.client_key_path,
                client_key.private_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PrivateFormat.PKCS8,
                    encryption_algorithm=serialization.NoEncryption(),
                ),
                mode=0o600,
            )

            return csr.public_bytes(serialization.Encoding.PEM)
        except Exception as e:
            self._logger.error("Client CSR generation error", error=str(e))
            return None

    def sign_client_csr(
        self, csr_pem: bytes, uid: Optional[str] = None
    ) -> Optional[bytes]:
        """Sign a client CSR with the CA, producing a client leaf certificate.

        The certificate's Common Name is forced to ``uid`` regardless of what
        the CSR requested, so the server controls the identity binding. When
        ``uid`` is None it is taken from the CSR subject Common Name (so this
        method can be wired directly as a single-argument signer callback). The
        client private key is never seen here. Returns the signed cert (PEM),
        or None on failure.
        """
        try:
            csr = x509.load_pem_x509_csr(csr_pem, default_backend())
            if not csr.is_signature_valid:
                self._logger.log("Client CSR signature invalid", Logger.WARNING)
                return None

            if uid is None:
                cn = csr.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
                if not cn:
                    self._logger.log(
                        "Client CSR has no Common Name to bind", Logger.WARNING
                    )
                    return None
                uid = cn[0].value
                if isinstance(uid, bytes):
                    uid = uid.decode("utf-8")

            with open(self.ca_key_path, "rb") as f:
                ca_key = serialization.load_pem_private_key(
                    f.read(), password=None, backend=default_backend()
                )
            with open(self.ca_cert_path, "rb") as f:
                ca_cert = x509.load_pem_x509_certificate(f.read(), default_backend())

            # Force CN = uid: the server decides the identity, not the client.
            subject = x509.Name(
                [
                    x509.NameAttribute(NameOID.COUNTRY_NAME, "IT"),
                    x509.NameAttribute(
                        NameOID.ORGANIZATION_NAME, ApplicationConfig.service_name
                    ),
                    x509.NameAttribute(NameOID.COMMON_NAME, uid),
                ]
            )

            client_cert = (
                x509.CertificateBuilder()
                .subject_name(subject)
                .issuer_name(ca_cert.subject)
                .public_key(csr.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(datetime.datetime.now(datetime.UTC))
                .not_valid_after(
                    datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=365)
                )
                .add_extension(
                    x509.BasicConstraints(ca=False, path_length=None),
                    critical=True,
                )
                .add_extension(
                    x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]),
                    critical=False,
                )
                .sign(ca_key, hashes.SHA256(), default_backend())  # ty:ignore[invalid-argument-type]
            )

            return client_cert.public_bytes(serialization.Encoding.PEM)
        except Exception as e:
            self._logger.error("Client CSR signing error", error=str(e))
            return None

    def save_client_certificate(self, cert_data: bytes | str) -> bool:
        """Persist the CA-signed client leaf certificate (public, mode 0o644)."""
        try:
            if isinstance(cert_data, str):
                cert_data = cert_data.encode("utf-8")
            atomic_write_bytes(self.client_cert_path, cert_data, mode=0o644)
            return True
        except Exception as e:
            self._logger.error("Error saving client certificate", error=str(e))
            return False

    def client_credentials_exist(self) -> bool:
        """True if this machine holds a client cert + key for mutual TLS."""
        return self.client_cert_path.exists() and self.client_key_path.exists()

    def get_client_credentials(self) -> Tuple[Optional[str], Optional[str]]:
        """Return the (cert, key) paths for the client identity, or (None, None)."""
        if self.client_credentials_exist():
            return str(self.client_cert_path), str(self.client_key_path)
        return None, None

    def remove_client_credentials(self) -> bool:
        """Delete the local client cert + key (used before re-pairing)."""
        removed = False
        for path in (self.client_cert_path, self.client_key_path):
            try:
                path.unlink()
                removed = True
            except FileNotFoundError:
                pass
            except OSError as e:
                self._logger.log(
                    f"Error removing client credential {path}: {e}", Logger.WARNING
                )
        return removed

    def certificates_exist(self) -> bool:
        """
        Check if all certificates are already present (CA and server)
        """
        return (
            self.ca_cert_path.exists()
            and self.ca_key_path.exists()
            and self.server_cert_path.exists()
            and self.server_key_path.exists()
        )

    def peer_certificate_exists(self, source_id: Optional[str] = None) -> bool:
        """Return True if a CA certificate is pinned for the given peer.

        With ``source_id=None`` falls back to the local CA cert existence
        (used by the client before any pairing has happened).
        """
        if source_id is None:
            return self.ca_cert_path.exists()

        mapping = self._load_cert_mapping()
        cert_file = mapping.get(source_id)
        if cert_file:
            return (self.cert_dir / cert_file).exists()
        return False

    def export_ca_for_client(self, export_path: str) -> bool:
        """Export CA certificate for distribution to clients"""
        try:
            import shutil

            shutil.copy(self.ca_cert_path, export_path)
            return True
        except Exception as e:
            self._logger.error("CA export error", error=str(e))
            return False

    def get_server_credentials(self) -> Tuple[Optional[str], Optional[str]]:
        """Return the paths of server certificates"""
        if self.server_cert_path.exists() and self.server_key_path.exists():
            return str(self.server_cert_path), str(self.server_key_path)
        return None, None

    def get_ca_cert_path(self, source_id: Optional[str] = None) -> Optional[str]:
        """Return the path of CA certificate for a specific server"""
        if source_id is None:
            if self.ca_cert_path.exists():
                return str(self.ca_cert_path)
            return None

        mapping = self._load_cert_mapping()
        cert_file = mapping.get(source_id)
        if cert_file:
            cert_path = self.cert_dir / cert_file
            if cert_path.exists():
                return str(cert_path)
        return None

    def load_ca_data(self, source_id: Optional[str] = None) -> Optional[bytes]:
        """Load CA certificate data for a specific server"""
        cert_path = self.get_ca_cert_path(source_id)
        if cert_path is None:
            return None

        try:
            with open(cert_path, "rb") as f:
                return f.read()
        except Exception as e:
            self._logger.error("Error loading CA data", error=str(e))
            return None

    def save_ca_data(self, data: bytes | str, source_id: str) -> bool:
        """Save CA certificate data from a specific server"""
        try:
            if isinstance(data, str):
                data = data.encode("utf-8")

            # Create a unique filename for this server
            cert_filename = f"ca_{source_id.replace(':', '_').replace('.', '_')}.crt"
            cert_path = self.cert_dir / cert_filename

            # Save certificate data atomically (public cert from a peer).
            if isinstance(data, str):
                data = data.encode()

            atomic_write_bytes(cert_path, data, mode=0o644)

            # Update mapping
            mapping = self._load_cert_mapping()
            mapping[source_id] = cert_filename
            self._save_cert_mapping(mapping)

            self._logger.log(
                f"Saved CA certificate for server: {source_id}", Logger.INFO
            )
            return True
        except Exception as e:
            self._logger.error("Error saving CA data", error=str(e))
            return False

    def remove_ca_data(self, *source_ids: Optional[str]) -> bool:
        """Delete the saved CA certificate(s) referenced by any of the given
        identifiers.

        The save path stores a CA under one source-id key (typically the
        server UID) and ``extend_mapping`` adds aliases under additional
        keys (resolved IP, hostname). Pass every identifier the caller
        knows about; this method:

        1. Resolves each non-empty ``source_id`` against the mapping.
        2. Unlinks the referenced cert file(s) on disk.
        3. Removes every mapping entry that pointed at any of those files,
           wiping orphan aliases in the same pass.

        Returns True if at least one file or mapping entry was removed.
        """
        try:
            mapping = self._load_cert_mapping()
            files_to_remove: set[str] = set()
            ids_removed: list[str] = []

            for sid in source_ids:
                if not sid:
                    continue
                cert_filename = mapping.get(sid)
                if cert_filename:
                    files_to_remove.add(cert_filename)
                    ids_removed.append(sid)

            # Also drop any alias pointing at one of those files - handles
            # the resolved-IP alias added by extend_mapping.
            if files_to_remove:
                for key, filename in list(mapping.items()):
                    if filename in files_to_remove:
                        mapping.pop(key, None)

            for filename in files_to_remove:
                try:
                    (self.cert_dir / filename).unlink()
                except FileNotFoundError:
                    pass
                except OSError as e:
                    self._logger.log(
                        f"Error removing CA cert file {filename}: {e}",
                        Logger.WARNING,
                    )

            if files_to_remove or ids_removed:
                self._save_cert_mapping(mapping)
                self._logger.log(
                    f"Removed CA certificate(s): files={sorted(files_to_remove)}, "
                    f"identifiers={ids_removed}",
                    Logger.INFO,
                )
                return True

            self._logger.log(
                f"No CA certificate found for any of: {[s for s in source_ids if s]}",
                Logger.DEBUG,
            )
            return False
        except Exception as e:
            self._logger.error("Error removing CA data", error=str(e))
            return False

    def extend_mapping(
        self, source_id: Optional[str], cert_filename: Optional[str]
    ) -> bool:
        """
        Extends the certificate mapping by adding or updating the mapping between
        a source ID and a certificate filename. Validates that neither of the
        arguments is None and updates the mapping only if both are provided.

        Arguments:
            source_id: str
                The unique identifier for the source to be mapped to a
                certificate filename.

            cert_filename: Optional[str]
                The certificate filename to be associated with the source ID.
                Can be None, which results in an unsuccessful operation.

        Returns:
            bool
                True if the mapping was successfully extended and saved; False
                if the operation failed due to invalid arguments or an error
                during the process.
        """
        if source_id is None or cert_filename is None:
            return False

        try:
            mapping = self._load_cert_mapping()
            mapping[source_id] = cert_filename
            return self._save_cert_mapping(mapping)
        except Exception as e:
            self._logger.error("Error extending cert mapping", error=str(e))
            return False

    def _get_cert_mapping_path(self) -> Path:
        """Get the path to the certificate mapping file"""
        return self.cert_dir / "cert_mapping.json"

    def _load_cert_mapping(self) -> Dict[str, str]:
        """Load the certificate mapping from file"""
        mapping_path = self._get_cert_mapping_path()
        if mapping_path.exists():
            try:
                with open(mapping_path, "r") as f:
                    content = f.read()
                    return _decoder.decode(content.encode())
            except Exception as e:
                self._logger.error("Error loading cert mapping", error=str(e))
        return {}

    def _save_cert_mapping(self, mapping: Dict[str, str]) -> bool:
        """Save the certificate mapping to file"""
        try:
            atomic_write_bytes(
                self._get_cert_mapping_path(),
                _encoder.encode(mapping),
            )
            return True
        except Exception as e:
            self._logger.error("Error saving cert mapping", error=str(e))
            return False
