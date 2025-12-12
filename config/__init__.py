from dataclasses import dataclass, field
from typing import Optional


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

    # Data exchange params
    max_chunk_size: int = 1024  # 1 KB
    max_delay_tolerance: float = 0.1
    parallel_processors: int = 1
    auto_chunk: bool = True

    config_files: dict = field(default_factory=dict)

    def __post_init__(self):
        self.config_files = {
            "server": self.config_file,
            "client": self.client_config_file,
        }

    def get_config_dir(self) -> str:
        return self.config_path

    def get_certificate_path(self) -> str:
        return self.get_config_dir() + self.ssl_path

class ServerConfig:
    """
    Server configuration settings
    Exposes API to handle: streams enabled, SSL certificates, clients infos, logging levels.
    """
    def __init__(self, app_config: ApplicationConfig = ApplicationConfig()):
        """
        Initializes the ServerConfig with default settings.
        Args:
            app_config (ApplicationConfig): The application configuration instance.
        """
        self.app_config = app_config
        self.streams_enabled = {}
        self.is_ssl_enabled = False

    def enable_ssl(self):
        self.is_ssl_enabled = True

    def disable_ssl(self):
        self.is_ssl_enabled = False

    def enable_stream(self, stream_type: int):
        self.streams_enabled[stream_type] = True

    def disable_stream(self, stream_type: int):
        self.streams_enabled[stream_type] = False

    def is_stream_enabled(self, stream_type: int) -> bool:
        return self.streams_enabled.get(stream_type, False)

    def get_ssl_config(self) -> dict:
        return {
            "certfile": f"{self.app_config.ssl_path}{self.app_config.ssl_certfile}",
            "keyfile": f"{self.app_config.ssl_path}{self.app_config.ssl_keyfile}",
        }

class ClientConfig:
    """
    Client configuration settings
    Exposes API to handle: server connection info, logging levels, streams enabled.
    """
    def __init__(self, app_config: ApplicationConfig = ApplicationConfig()):
        self.app_config = app_config
        self.streams_enabled = {}

    def enable_stream(self, stream_type: int):
        self.streams_enabled[stream_type] = True

    def disable_stream(self, stream_type: int):
        self.streams_enabled[stream_type] = False

    def is_stream_enabled(self, stream_type: int) -> bool:
        return self.streams_enabled.get(stream_type, False)


@dataclass
class ServerConnectionConfig:
    """
    Represents the configuration for a server connection.

    This class is used to manage and specify the configuration settings needed
    when establishing a server connection. It includes options for defining
    the host, port, heartbeat interval, and support for SSL/TLS certificates as
    well as enabling or disabling SSL functionality.

    Attributes:
        host:
            Hostname or IP address of the server to connect to. Defaults to "0.0.0.0".
        port:
            Port number to connect to. Defaults to 5555.
        heartbeat_interval:
            Time interval in seconds to send heartbeat signals to the server.
            Defaults to 1 second.
        certfile:
            Path to the SSL certificate file. If None, SSL is not configured.
            Defaults to None.
        keyfile:
            Path to the SSL key file. If None, SSL is not configured. Defaults to None.
        ssl_enabled:
            A boolean indicating whether SSL/TLS is enabled for the connection.
            Defaults to False.
    """
    host: str = "0.0.0.0" #Can be an hostname or an IP address
    port: int = 5555
    heartbeat_interval: int = 1
    certfile: Optional[str] = None
    keyfile: Optional[str] = None
    ssl_enabled: bool = False


@dataclass
class ClientConnectionConfig:
    """
    Represents the configuration settings for a client connection.

    This class is utilized to define and store the configuration parameters required for
    a client to connect to a server. These parameters include server details such as host
    and port, client-specific settings like hostname and heartbeat interval, and
    optional configurations for security and connectivity behaviors.

    Attributes:
        server_host: The hostname or IP address of the server to connect to.
        server_port: The port number of the server to connect to.
        client_hostname: The optional hostname of the client. Defaults to None if not specified.
        heartbeat_interval: The interval in seconds for sending heartbeat signals to the server.
        auto_reconnect: A flag indicating whether to automatically reconnect if the connection
            drops. Defaults to True.
        certfile: Path to the SSL certificate file for secure connections, or None if not
            using SSL.
    """
    server_host: str = "127.0.0.1"
    server_port: int = 5555
    client_hostname: Optional[str] = None
    heartbeat_interval: int = 1
    auto_reconnect: bool = True
    certfile: Optional[str] = None
