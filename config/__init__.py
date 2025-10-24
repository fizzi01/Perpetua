from dataclasses import dataclass, field

@dataclass
class ApplicationConfig:

    service_name: str = "PyContinuity"
    app_name: str = "PyContinuity"

    ssl_path: str = "ssl/"
    ssl_certfile: str = "certfile.pem"
    ssl_keyfile: str = "keyfile.key"

    config_file: str = "server_config.json"
    config_path: str = "config/"
    client_config_file: str = "client_config.json"

    server_key: str = "server"
    client_key: str = "client"

    config_files: dict = field(default_factory=dict)

    def __post_init__(self):
        self.config_files = {
            "server": self.config_file,
            "client": self.client_config_file,
        }