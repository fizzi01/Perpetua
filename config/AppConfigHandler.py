import os
import json
import platform
import shutil
import subprocess
import socket
import ipaddress
from typing import Tuple

import config
from utils import net

class ConfigFactory:
    @staticmethod
    def get_config_dir(app_name: str) -> str:
        """Restituisce il percorso della directory di configurazione basata sul sistema operativo."""
        if platform.system() == 'Windows':
            return os.path.join(os.getenv('LOCALAPPDATA'), app_name)
        elif platform.system() == 'Darwin':
            return os.path.join(os.path.expanduser('~/Library/Caches'), app_name)
        elif platform.system() == 'Linux':
            return os.path.join(os.path.expanduser('~/.config'), app_name)
        else:
            raise ValueError("Unsupported operating system.")


class AppConfigHandler:
    def __init__(self):
        self.app_name = config.APP_NAME
        self.cache_dir = ConfigFactory.get_config_dir(self.app_name)
        self.config_dir = os.path.join(self.cache_dir, config.CONFIG_PATH)
        self.ssl_dir = os.path.join(self.cache_dir, config.SSL_PATH)
        self.ssl_cert = config.SSL_CERTFILE_NAME
        self.ssl_key = config.SSL_KEYFILE_NAME
        self.config_files = config.CONFIG_FILES

    def load_config(self, config_name: str) -> dict:
        """Carica un file di configurazione specifico."""
        config_file = self.config_files.get(config_name)
        if not config_file:
            raise ValueError(f"Configuration file for {config_name} not found.")

        config_path = os.path.join(self.config_dir, config_file)
        if not os.path.exists(config_path):
            print(f"Configuration file {config_path} not found.")
            return {}

        with open(config_path, 'r') as file:
            config_data = json.load(file)
        print(f"Configuration loaded from {config_path}")
        return config_data

    def save_config(self, config_name: str, config_data: dict):
        """Salva i dati in un file di configurazione specifico."""
        config_file = self.config_files.get(config_name)
        if not config_file:
            raise ValueError(f"Configuration file for {config_name} not found.")

        os.makedirs(self.config_dir, exist_ok=True)
        config_path = os.path.join(self.config_dir, config_file)

        with open(config_path, 'w') as file:
            json.dump(config_data, file, indent=4)
        print(f"Configuration saved to {config_path}")

    def generate_ssl_certificate(self, force: bool = False):
        """Genera un certificato SSL con rilevamento automatico della subnet."""
        if not shutil.which("openssl"):
            raise EnvironmentError("OpenSSL is not installed. Please install it to generate SSL certificates.")

        os.makedirs(self.ssl_dir, exist_ok=True)
        cert_path = os.path.join(self.ssl_dir, self.ssl_cert)
        key_path = os.path.join(self.ssl_dir, self.ssl_key)
        cnf_path = os.path.join(self.ssl_dir, 'openssl.cnf')

        if os.path.exists(cert_path) and os.path.exists(key_path) and not force:
            print("SSL certificate already exists.")
            return cert_path, key_path

        subnet_ips = self._get_local_ips_in_subnet()
        self._write_openssl_cnf(cnf_path, subnet_ips)

        print("Generating SSL certificate...")
        subprocess.run([
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-keyout", key_path,
            "-out", cert_path, "-days", "365", "-nodes", "-config", cnf_path,
            "-subj", "/C=US/ST=California/L=San Francisco/O=My Company/CN=localhost"
        ], check=True)
        print(f"SSL certificate generated: {cert_path}, {key_path}")
        return cert_path, key_path

    def _get_local_ips_in_subnet(self) -> list:
        """Rileva automaticamente gli IP locali nella subnet."""
        local_ips = []
        try:
            local_ip = net.get_local_ip()
            subnet = ipaddress.IPv4Network(f"{local_ip}/24", strict=False)
            local_ips = [str(ip) for ip in subnet]
            # Filtra gli IP riservati (es: .0, .1, .255)
            local_ips = [ip for ip in local_ips if not ip.endswith(".0") and not ip.endswith(".1") and not ip.endswith(".255")]
        except Exception as e:
            print(f"Error detecting subnet: {e}")
            local_ips = ["127.0.0.1", "::1"]
        return local_ips

    def _write_openssl_cnf(self, cnf_path: str, ips: list):
        """Scrive il file di configurazione OpenSSL."""
        alt_names = [f"IP.{i + 1} = {ip}" for i, ip in enumerate(ips)]
        with open(cnf_path, 'w') as cnf_file:
            cnf_file.write(f"""\
            [ req ]
            default_bits       = 2048
            default_md         = sha256
            distinguished_name = req_distinguished_name
            req_extensions     = req_ext
            x509_extensions    = v3_ca
            
            [ req_distinguished_name ]
            commonName                  = Common Name (e.g. server FQDN or YOUR name)
            commonName_default          = localhost
            
            [ req_ext ]
            subjectAltName = @alt_names
            
            [ v3_ca ]
            subjectAltName = @alt_names
            
            [ alt_names ]
            {os.linesep.join(alt_names)}
            """)

    def load_ssl_certificate(self, force: bool = False) -> Tuple[str, str]:
        """Carica il certificato SSL, rigenerandolo se necessario."""
        certfile = os.path.join(self.ssl_dir, self.ssl_cert)
        keyfile = os.path.join(self.ssl_dir, self.ssl_key)
        if not os.path.exists(certfile) or not os.path.exists(keyfile) or force:
            return self.generate_ssl_certificate(force=force)
        print(f"SSL certificate loaded: {certfile}, {keyfile}")
        return certfile, keyfile