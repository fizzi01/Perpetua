import os
import json
import platform

import config

import shutil
import subprocess


class AppConfigHandler:
    def __init__(self):
        self.app_name = config.APP_NAME
        self.config_dir = config.CONFIG_PATH
        self.ssl_dir = config.SSL_PATH
        self.ssl_cert = config.SSL_CERTFILE_NAME
        self.ssl_key = config.SSL_KEYFILE_NAME
        self.config_files = config.CONFIG_FILES

    def get_config_dir(self):
        if platform.system() == 'Windows':
            return os.path.join(os.getenv('LOCALAPPDATA'), self.app_name)
        elif platform.system() == 'Darwin':
            return os.path.join(os.path.expanduser('~/Library/Caches'), self.app_name)
        elif platform.system() == 'Linux':
            return os.path.join(os.path.expanduser('~/.config'), self.app_name)

    def load_config(self, config_name: str) -> dict:
        config_file = self.config_files.get(config_name)
        config_file_path = os.path.join(self.get_config_dir(), self.config_dir)
        if not config_file:
            raise ValueError(f"Configuration file for {config_name} not found.")

        # Check if directory exists
        if not os.path.exists(config_file_path):
            os.makedirs(config_file_path, exist_ok=True)

        config_path = os.path.join(self.get_config_dir(), self.config_dir, config_file)
        if not os.path.exists(config_path):
            print(f"Configuration file {config_path} not found.")
            return {}

        with open(config_path) as file:
            config_data = json.load(file)
        print(f"Configuration loaded from {config_path}")
        return config_data

    def save_config(self, config_name: str, config_data: dict):
        config_file = self.config_files.get(config_name)
        if not config_file:
            raise ValueError(f"Configuration file for {config_name} not found.")

        config_path = os.path.join(self.get_config_dir(), self.config_dir, config_file)
        if not os.path.exists(self.get_config_dir()):
            os.makedirs(self.get_config_dir(), exist_ok=True)

        with open(config_path, 'w') as file:
            json.dump(config_data, file, indent=4)
        print(f"Configuration saved to {config_path}")

    @staticmethod
    def generate_ssl_certificate(self, server_ip: str = "", force: bool = False):
        if os.name == 'nt':
            return

        # Check if openssl is installed
        if not shutil.which("openssl"):
            print("Please install openssl to generate SSL certificates.")
            return

        # Check if directory exists
        if not os.path.exists(os.path.join(self.get_config_dir(), self.ssl_dir)):
            os.makedirs(os.path.join(self.get_config_dir(), self.ssl_dir), exist_ok=True)

        cert_path = os.path.join(self.get_config_dir(), self.ssl_dir, self.ssl_cert)
        key_path = os.path.join(self.get_config_dir(), self.ssl_dir, self.ssl_key)
        cnf_path = os.path.join(self.get_config_dir(), self.ssl_dir, 'openssl.cnf')

        if os.path.exists(cnf_path):
            with open(cnf_path) as cnf_file:
                # Check if server_ip is in the file
                if server_ip:
                    if server_ip in cnf_file.read():
                        return cert_path, key_path
                    else:
                        print("SSL certificate changed. Regenerating...")

        # Create the openssl.cnf file
        with open(cnf_path, 'w') as cnf_file:
            cnf_file.write("""\
        [ req ]
        default_bits       = 2048
        distinguished_name = req_distinguished_name
        req_extensions     = req_ext
        x509_extensions    = v3_ca

        [ req_distinguished_name ]
        countryName                 = Country Name (2 letter code)
        countryName_default         = US
        stateOrProvinceName         = State or Province Name (full name)
        stateOrProvinceName_default = California
        localityName                = Locality Name (eg, city)
        localityName_default        = San Francisco
        organizationName            = Organization Name (eg, company)
        organizationName_default    = My Company
        commonName                  = Common Name (e.g. server FQDN or YOUR name)
        commonName_default          = localhost

        [ req_ext ]
        subjectAltName = @alt_names

        [ v3_ca ]
        subjectAltName = @alt_names

        [ alt_names ]
        DNS.1 = localhost
        IP.1 = 0.0.0.0
        IP.2 = 127.0.0.1
        IP.3 = ::1
        """)
            # Add the server IP to the alt_names
            if server_ip:
                cnf_file.write(f"IP.4 = {server_ip}\n")

        if (not os.path.exists(cert_path) or not os.path.exists(key_path)) or force:
            print("Generating SSL certificate...")
            subprocess.run([
                "openssl", "req", "-x509", "-newkey", "rsa:4096", "-keyout", key_path,
                "-out", cert_path, "-days", "365", "-nodes", "-config", cnf_path,
                "-subj", f"/C=US/ST=California/L=San Francisco/O=My Company/CN={server_ip}"
            ], stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
            print("SSL certificate generated.")

    def save_ssl_certificate(self, cert_data: str, key_data: str):
        certfile = os.path.join(self.get_config_dir(), self.ssl_dir, 'cert.pem')
        keyfile = os.path.join(self.get_config_dir(), self.ssl_dir, 'key.pem')
        with open(certfile, 'w') as cert_file:
            cert_file.write(cert_data)
        with open(keyfile, 'w') as key_file:
            key_file.write(key_data)
        print(f"SSL certificate saved to {certfile} and {keyfile}")

    def load_ssl_certificate(self, server_ip: str = "", force: bool = False) -> tuple:
        """
        Load SSL certificate from file
        :return: Tuple of certificate and key file paths (certfile, keyfile)
        """
        certfile = os.path.join(self.get_config_dir(), self.ssl_dir, config.SSL_CERTFILE_NAME)
        keyfile = os.path.join(self.get_config_dir(), self.ssl_dir, config.SSL_KEYFILE_NAME)
        if (not os.path.exists(certfile) or not os.path.exists(keyfile)) or force:
            self.generate_ssl_certificate(self, server_ip=server_ip, force=force)
        print(f"SSL certificate loaded from {certfile} and {keyfile}")
        return certfile, keyfile
