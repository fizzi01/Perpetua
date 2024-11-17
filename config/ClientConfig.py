class ClientConfig:
    def __init__(self, server_ip, server_port, use_ssl=False, certfile=None, logging=False, wait=0):
        self.server_ip = server_ip
        self.server_port = server_port
        self.use_ssl = use_ssl
        self.server_certfile = certfile
        self.logging = logging
        self.wait = wait

    def get_wait(self):
        return self.wait

    def get_server_ip(self):
        return self.server_ip

    def get_server_port(self):
        return self.server_port

    def get_use_ssl(self):
        return self.use_ssl

    def get_server_certfile(self):
        return self.server_certfile

    def get_logging(self):
        return self.logging

    def set_server_ip(self, server_ip: str):
        self.server_ip = server_ip

    def set_server_port(self, server_port: int):
        self.server_port = server_port

    def set_use_ssl(self, use_ssl: bool):
        self.use_ssl = use_ssl

    def set_server_certfile(self, certfile: str):
        self.server_certfile = certfile

    def set_logging(self, logging: bool):
        self.logging = logging

    def set_wait(self, wait: int):
        self.wait = wait


