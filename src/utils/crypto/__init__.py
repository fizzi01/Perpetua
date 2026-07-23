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
from typing import Tuple, Optional, Dict, Any
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

# Backdate every certificate's ``notBefore`` by this margin. Peers on a LAN
# often run without NTP and can have clocks skewed by minutes (occasionally
# more). Without this margin a peer whose clock trails the issuer rejects the
# freshly-minted chain with "certificate is not yet valid". Backdating costs
# nothing security-wise (the certs live for 365+ days) and is exactly what
# public CAs do. Kept generous to tolerate manually mis-set clocks.
CLOCK_SKEW_TOLERANCE = datetime.timedelta(hours=3)


def _validity_window(
    lifetime: datetime.timedelta,
) -> Tuple[datetime.datetime, datetime.datetime]:
    """Return ``(not_before, not_after)`` for a certificate.

    ``not_before`` is backdated by :data:`CLOCK_SKEW_TOLERANCE` so a validating
    peer whose clock trails the issuer still sees the certificate as valid.

    ``not_after`` is computed relative to ``not_before`` (not to ``now``), so
    the total validity span stays exactly ``lifetime`` — the window is shifted
    earlier to absorb skew, not widened by it. The forward-looking validity is
    therefore ``lifetime - CLOCK_SKEW_TOLERANCE``, which is negligible against a
    365+ day lifetime.
    """
    not_before = datetime.datetime.now(datetime.UTC) - CLOCK_SKEW_TOLERANCE
    return not_before, not_before + lifetime


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

            ca_not_before, ca_not_after = _validity_window(
                datetime.timedelta(days=3650)
            )
            ca_cert = (
                x509.CertificateBuilder()
                .subject_name(subject)
                .issuer_name(issuer)
                .public_key(ca_key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(ca_not_before)
                .not_valid_after(ca_not_after)
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

            server_not_before, server_not_after = _validity_window(
                datetime.timedelta(days=365)
            )
            server_cert = (
                x509.CertificateBuilder()
                .subject_name(subject)
                .issuer_name(ca_cert.subject)
                .public_key(server_key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(server_not_before)
                .not_valid_after(server_not_after)
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

    # Placeholder CN used in the client CSR: the client does NOT choose its own
    # UID - the server assigns it at signing time and stamps it into the cert CN.
    CLIENT_CSR_PLACEHOLDER_CN = "unassigned"

    def generate_client_key_and_csr(self) -> Optional[bytes]:
        """Generate a client private key and a CSR for mutual-TLS identity.

        The private key is persisted locally (``client.key``, mode 0o600) and
        never leaves this machine. The CSR carries a placeholder Common Name -
        the client has no UID yet; the server generates one and forces it into
        the signed certificate. Returns the CSR (PEM), or None on failure.
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
                            x509.NameAttribute(
                                NameOID.COMMON_NAME, self.CLIENT_CSR_PLACEHOLDER_CN
                            ),
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

            client_not_before, client_not_after = _validity_window(
                datetime.timedelta(days=365)
            )
            client_cert = (
                x509.CertificateBuilder()
                .subject_name(subject)
                .issuer_name(ca_cert.subject)
                .public_key(csr.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(client_not_before)
                .not_valid_after(client_not_after)
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

    @staticmethod
    def read_certificate_common_name(cert_data: bytes | str) -> Optional[str]:
        """Return the subject Common Name of a PEM certificate, or None.

        Used by the client to learn the server-assigned UID from the leaf
        certificate it was issued at pairing.
        """
        try:
            if isinstance(cert_data, str):
                cert_data = cert_data.encode("utf-8")
            cert = x509.load_pem_x509_certificate(cert_data, default_backend())
            attrs = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
            if not attrs:
                return None
            cn = attrs[0].value
            return cn.decode("utf-8") if isinstance(cn, bytes) else cn
        except Exception:
            return None

    @staticmethod
    def _certificate_name_common_name(name: x509.Name) -> Optional[str]:
        attrs = name.get_attributes_for_oid(NameOID.COMMON_NAME)
        if not attrs:
            return None
        value = attrs[0].value
        return value.decode("utf-8") if isinstance(value, bytes) else value

    @staticmethod
    def _certificate_time_to_iso(cert: x509.Certificate, attr: str) -> str:
        value = getattr(cert, f"{attr}_utc", None)
        if value is None:
            value = getattr(cert, attr)
            if value.tzinfo is None:
                value = value.replace(tzinfo=datetime.UTC)
        return value.isoformat()

    @staticmethod
    def _public_key_info(cert: x509.Certificate) -> Tuple[str, Optional[int]]:
        public_key = cert.public_key()
        if isinstance(public_key, rsa.RSAPublicKey):
            return "RSA", public_key.key_size
        algorithm = public_key.__class__.__name__.replace("PublicKey", "")
        key_size = getattr(public_key, "key_size", None)
        return algorithm or "Unknown", key_size

    @classmethod
    def read_certificate_metadata(
        cls, cert_path: Optional[str | Path]
    ) -> Dict[str, Any]:
        """Return sanitized certificate metadata for UI/status payloads.

        This deliberately omits filesystem paths and raw PEM/key material.
        """
        if not cert_path:
            return {"present": False}

        path = Path(cert_path)
        if not path.exists():
            return {"present": False}

        try:
            with open(path, "rb") as f:
                cert = x509.load_pem_x509_certificate(f.read(), default_backend())

            algorithm, key_size = cls._public_key_info(cert)
            valid_until = cls._certificate_time_to_iso(cert, "not_valid_after")
            expires_at = datetime.datetime.fromisoformat(valid_until)

            return {
                "present": True,
                "subject_common_name": cls._certificate_name_common_name(cert.subject),
                "issuer_common_name": cls._certificate_name_common_name(cert.issuer),
                "valid_from": cls._certificate_time_to_iso(cert, "not_valid_before"),
                "valid_until": valid_until,
                "expired": datetime.datetime.now(datetime.UTC) > expires_at,
                "sha256_fingerprint": cert.fingerprint(hashes.SHA256()).hex().upper(),
                "public_key_algorithm": algorithm,
                "public_key_size": key_size,
            }
        except Exception:
            return {"present": False, "error": "unreadable"}

    def get_security_info(
        self, source_id: Optional[str] = None, ssl_enabled: bool = True
    ) -> Dict[str, Any]:
        """Return UI-safe details about the local mutual-TLS material."""
        server_ca = self.read_certificate_metadata(self.get_ca_cert_path(source_id))
        client_certificate = self.read_certificate_metadata(self.client_cert_path)
        private_key_present = self.client_key_path.exists()
        certificate_expired = bool(
            server_ca.get("expired") or client_certificate.get("expired")
        )
        mutual_tls_available = bool(
            ssl_enabled
            and server_ca.get("present")
            and client_certificate.get("present")
            and private_key_present
            and not certificate_expired
        )

        return {
            "ssl_enabled": ssl_enabled,
            "mutual_tls_available": mutual_tls_available,
            "server_ca": server_ca,
            "client_certificate": client_certificate,
            "private_key_present": private_key_present,
        }

    def get_client_uid(self) -> Optional[str]:
        """Return the client UID (the Common Name of the client certificate).

        This is the single source of truth for the client identity: it is read
        from ``client.crt`` when present, or None if no client certificate has
        been issued yet (unpaired client).
        """
        if not self.client_cert_path.exists():
            return None
        try:
            with open(self.client_cert_path, "rb") as f:
                return self.read_certificate_common_name(f.read())
        except Exception as e:
            self._logger.error("Error reading client UID", error=str(e))
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

    def get_server_cert_san(self) -> Tuple[list[str], list[str]]:
        """Return ``(ip_addresses, dns_names)`` from the server cert's SAN.

        Reads the Subject Alternative Name extension of ``server.crt`` and
        splits it into IP-address entries and DNS-name entries. Returns
        ``([], [])`` if the cert is missing or has no SAN. Used to detect when
        the machine's current IP is no longer covered by the leaf cert (e.g.
        after a DHCP rebind), so the server can re-issue the leaf.
        """
        if not self.server_cert_path.exists():
            return [], []
        try:
            with open(self.server_cert_path, "rb") as f:
                cert = x509.load_pem_x509_certificate(f.read(), default_backend())
            san = cert.extensions.get_extension_for_class(
                x509.SubjectAlternativeName
            ).value
            ips = [str(ip) for ip in san.get_values_for_type(x509.IPAddress)]
            dns = list(san.get_values_for_type(x509.DNSName))
            return ips, dns
        except x509.ExtensionNotFound:
            return [], []
        except Exception as e:
            self._logger.error("Error reading server certificate SAN", error=str(e))
            return [], []

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
