from pathlib import Path
from typing import Tuple, Optional
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.backends import default_backend
import datetime
import ipaddress

from config import ApplicationConfig
from utils.logging import Logger, get_logger


class CertificateManager:
    """Manages the generation and distribution of TLS certificates for LAN"""

    def __init__(self, cert_dir: str | Path = "./certs"):
        self.cert_dir = Path(cert_dir)
        self.cert_dir.mkdir(exist_ok=True, parents=True)

        self.ca_cert_path = self.cert_dir / "ca.crt"
        self.ca_key_path = self.cert_dir / "ca.key"
        self.server_cert_path = self.cert_dir / "server.crt"
        self.server_key_path = self.cert_dir / "server.key"

        self._logger = get_logger(self.__class__.__name__)

    def generate_ca(self, force: bool = False) -> bool:
        """Generate CA certificate if it doesn't exist"""
        if self.ca_cert_path.exists() and not force:
            return True

        try:
            # Generate CA private key
            ca_key = rsa.generate_private_key(
                public_exponent=65537,
                key_size=4096,
                backend=default_backend()
            )

            # Create CA certificate
            subject = issuer = x509.Name([
                x509.NameAttribute(NameOID.COUNTRY_NAME, "IT"),
                x509.NameAttribute(NameOID.ORGANIZATION_NAME, ApplicationConfig.service_name),
                x509.NameAttribute(NameOID.COMMON_NAME, f"{ApplicationConfig.service_name} CA"),
            ])

            ca_cert = x509.CertificateBuilder().subject_name(
                subject
            ).issuer_name(
                issuer
            ).public_key(
                ca_key.public_key()
            ).serial_number(
                x509.random_serial_number()
            ).not_valid_before(
                datetime.datetime.now(datetime.UTC)
            ).not_valid_after(
                datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=3650)
            ).add_extension(
                x509.BasicConstraints(ca=True, path_length=None),
                critical=True,
            ).sign(ca_key, hashes.SHA256(), default_backend())

            # Save CA key and certificate
            with open(self.ca_key_path, "wb") as f:
                f.write(ca_key.private_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PrivateFormat.PKCS8,
                    encryption_algorithm=serialization.NoEncryption()
                ))

            with open(self.ca_cert_path, "wb") as f:
                f.write(ca_cert.public_bytes(serialization.Encoding.PEM))

            return True
        except Exception as e:
            self._logger.log(f"CA generation error: {e}", Logger.ERROR)
            return False

    def generate_server_certificate(self, hostname: str, ip_addresses: list[str],
                                   force: bool = False) -> bool:
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
                ca_cert = x509.load_pem_x509_certificate(
                    f.read(), default_backend()
                )

            # Generate server private key
            server_key = rsa.generate_private_key(
                public_exponent=65537,
                key_size=2048,
                backend=default_backend()
            )

            # Create Subject Alternative Names (SAN)
            san_list = [x509.DNSName(hostname)]
            for ip in ip_addresses:
                try:
                    san_list.append(x509.IPAddress(ipaddress.ip_address(ip)))
                except ValueError:
                    san_list.append(x509.DNSName(ip))

            # Create server certificate
            subject = x509.Name([
                x509.NameAttribute(NameOID.COUNTRY_NAME, "IT"),
                x509.NameAttribute(NameOID.ORGANIZATION_NAME, ApplicationConfig.service_name),
                x509.NameAttribute(NameOID.COMMON_NAME, hostname),
            ])

            server_cert = x509.CertificateBuilder().subject_name(
                subject
            ).issuer_name(
                ca_cert.subject
            ).public_key(
                server_key.public_key()
            ).serial_number(
                x509.random_serial_number()
            ).not_valid_before(
                datetime.datetime.now(datetime.UTC)
            ).not_valid_after(
                datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=365)
            ).add_extension(
                x509.SubjectAlternativeName(san_list),
                critical=False,
            ).add_extension(
                x509.BasicConstraints(ca=False, path_length=None),
                critical=True,
            ).sign(ca_key, hashes.SHA256(), default_backend())

            # Save server key and certificate
            with open(self.server_key_path, "wb") as f:
                f.write(server_key.private_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PrivateFormat.PKCS8,
                    encryption_algorithm=serialization.NoEncryption()
                ))

            with open(self.server_cert_path, "wb") as f:
                f.write(server_cert.public_bytes(serialization.Encoding.PEM))

            return True
        except Exception as e:
            self._logger.log(f"Server certificate generation error: {e}", Logger.ERROR)
            return False

    def certificates_exist(self) -> bool:
        """
        Check if all certificates are already present (CA and server)
        """
        return (self.ca_cert_path.exists() and self.ca_key_path.exists() and
                self.server_cert_path.exists() and self.server_key_path.exists())

    def certificate_exist(self) -> bool:
        """
        Check if client CA certificate is already present
        """
        return self.ca_cert_path.exists()

    def export_ca_for_client(self, export_path: str) -> bool:
        """Export CA certificate for distribution to clients"""
        try:
            import shutil
            shutil.copy(self.ca_cert_path, export_path)
            return True
        except Exception as e:
            self._logger.log(f"CA export error: {e}", Logger.ERROR)
            return False

    def get_server_credentials(self) -> Tuple[Optional[str], Optional[str]]:
        """Return the paths of server certificates"""
        if self.server_cert_path.exists() and self.server_key_path.exists():
            return str(self.server_cert_path), str(self.server_key_path)
        return None, None

    def get_ca_cert_path(self) -> Optional[str]:
        """Return the path of CA certificate"""
        if self.ca_cert_path.exists():
            return str(self.ca_cert_path)
        return None

    def load_ca_data(self) -> Optional[bytes]:
        """Load CA certificate data for client side"""
        try:
            with open(self.ca_cert_path, "rb") as f:
                return f.read()
        except Exception as e:
            self._logger.log(f"Error loading CA data: {e}", Logger.ERROR)
            return None

    def save_ca_data(self, data: bytes | str) -> bool:
        """Save CA certificate data from client side"""
        try:
            with open(self.ca_cert_path, "wb") as f:
                f.write(data)
            return True
        except Exception as e:
            self._logger.log(f"Error saving CA data: {e}", Logger.ERROR)
            return False