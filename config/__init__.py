"""
Unified configuration management system for PyContinuity.
Handles server and client configurations with persistent storage support.
"""
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
import json
import os


from model.client import ClientObj
from utils.logging import Logger

@dataclass
class ApplicationConfig:
    """Application-wide configuration settings"""

    service_name: str = "PyContinuity"
    app_name: str = "PyContinuity"

    ssl_path: str = "ssl/"
    ssl_certfile: str = "certfile.pem"
    ssl_keyfile: str = "keyfile.key"

    server_config_file: str = "server_config.json"
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
            "server": self.server_config_file,
            "client": self.client_config_file,
        }

    def get_config_dir(self) -> str:
        return self.config_path

    def get_certificate_path(self) -> str:
        return self.get_config_dir() + self.ssl_path


class ServerConfig:
    """
    Server configuration settings with persistent storage support.
    Manages streams, SSL, connection settings, logging, and authorized clients.
    """

    # Default values
    DEFAULT_HOST = "0.0.0.0"
    DEFAULT_PORT = 5555
    DEFAULT_HEARTBEAT_INTERVAL = 1
    DEFAULT_LOG_LEVEL = Logger.INFO

    def __init__(self, app_config: Optional[ApplicationConfig] = None, config_file: Optional[str] = None):
        """
        Initializes the ServerConfig with default settings.

        Args:
            app_config: The application configuration instance.
            config_file: Path to the configuration file. If None, uses default from app_config.
        """
        self.app_config = app_config or ApplicationConfig()
        self.config_file = config_file or os.path.join(
            self.app_config.get_config_dir(),
            self.app_config.server_config_file
        )

        # Connection settings
        self.host: str = self.DEFAULT_HOST
        self.port: int = self.DEFAULT_PORT
        self.heartbeat_interval: int = self.DEFAULT_HEARTBEAT_INTERVAL

        # Stream management
        self.streams_enabled: Dict[int, bool] = {}

        # SSL configuration (managed by CertificateManager)
        self.ssl_enabled: bool = False

        # Logging configuration
        self.log_level: int = self.DEFAULT_LOG_LEVEL
        self.log_to_file: bool = False
        self.log_file_path: Optional[str] = None

        # Authorized clients
        self.authorized_clients: List[Dict[str, Any]] = []

    # SSL Configuration
    def enable_ssl(self) -> None:
        """Enable SSL (certificates managed by CertificateManager)"""
        self.ssl_enabled = True

    def disable_ssl(self) -> None:
        """Disable SSL"""
        self.ssl_enabled = False

    # Stream Management
    def enable_stream(self, stream_type: int) -> None:
        """Enable a specific stream type"""
        self.streams_enabled[stream_type] = True

    def disable_stream(self, stream_type: int) -> None:
        """Disable a specific stream type"""
        self.streams_enabled[stream_type] = False

    def is_stream_enabled(self, stream_type: int) -> bool:
        """Check if a stream type is enabled"""
        return self.streams_enabled.get(stream_type, False)

    # Connection Configuration
    def set_connection_params(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        heartbeat_interval: Optional[int] = None
    ) -> None:
        """Update connection parameters"""
        if host is not None:
            self.host = host
        if port is not None:
            self.port = port
        if heartbeat_interval is not None:
            self.heartbeat_interval = heartbeat_interval

    # Logging Configuration
    def set_logging(
        self,
        level: Optional[int] = None,
        log_to_file: Optional[bool] = None,
        log_file_path: Optional[str] = None
    ) -> None:
        """Configure logging settings"""
        if level is not None:
            self.log_level = level
        if log_to_file is not None:
            self.log_to_file = log_to_file
        if log_file_path is not None:
            self.log_file_path = log_file_path

    # Client Management
    def add_authorized_client(self, client_info: Dict[str, Any]) -> None:
        """
        Add an authorized client (avoids duplicates).

        Args:
            client_info: Dictionary with keys: hostname, ip_address, screen_position,
                        screen_resolution, client_name, etc.
        """
        hostname = client_info.get('hostname')
        ip_address = client_info.get('ip_address')

        # Check for duplicates
        is_duplicate = any(
            (c.get('hostname') == hostname and hostname) or
            (c.get('ip_address') == ip_address and ip_address)
            for c in self.authorized_clients
        )

        if not is_duplicate:
            self.authorized_clients.append(client_info)

    def remove_authorized_client(
        self,
        hostname: Optional[str] = None,
        ip_address: Optional[str] = None
    ) -> None:
        """Remove an authorized client by hostname or IP address"""
        self.authorized_clients = [
            c for c in self.authorized_clients
            if not ((c.get('hostname') == hostname and hostname) or
                   (c.get('ip_address') == ip_address and ip_address))
        ]

    def get_authorized_clients(self) -> List[Dict[str, Any]]:
        """Get list of authorized clients"""
        return self.authorized_clients.copy()

    # Serialization
    def to_dict(self) -> Dict[str, Any]:
        """Convert configuration to dictionary for serialization"""
        return {
            "host": self.host,
            "port": self.port,
            "heartbeat_interval": self.heartbeat_interval,
            "streams_enabled": self.streams_enabled,
            "ssl_enabled": self.ssl_enabled,
            "log_level": self.log_level,
            "log_to_file": self.log_to_file,
            "log_file_path": self.log_file_path,
            "authorized_clients": self.authorized_clients
        }

    def from_dict(self, data: Dict[str, Any]) -> None:
        """Load configuration from dictionary"""
        self.host = data.get("host", self.host)
        self.port = data.get("port", self.port)
        self.heartbeat_interval = data.get("heartbeat_interval", self.heartbeat_interval)

        # Load streams and convert string keys to int if necessary
        streams = data.get("streams_enabled", {})
        self.streams_enabled = {int(k): v for k, v in streams.items()}

        self.ssl_enabled = data.get("ssl_enabled", self.ssl_enabled)
        self.log_level = data.get("log_level", self.log_level)
        self.log_to_file = data.get("log_to_file", self.log_to_file)
        self.log_file_path = data.get("log_file_path", self.log_file_path)
        self.authorized_clients = data.get("authorized_clients", [])

    # Persistence
    def save(self, file_path: Optional[str] = None) -> None:
        """
        Save configuration to JSON file.

        Args:
            file_path: Path to save the configuration. Uses self.config_file if None.
        """
        file_path = file_path or self.config_file

        # Ensure directory exists
        dir_path = os.path.dirname(file_path)
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)

        with open(file_path, 'w') as f:
            json.dump(self.to_dict(), f, indent=4)

    def load(self, file_path: Optional[str] = None) -> bool:
        """
        Load configuration from JSON file.

        Args:
            file_path: Path to load the configuration from. Uses self.config_file if None.

        Returns:
            True if loaded successfully, False if file doesn't exist
        """
        file_path = file_path or self.config_file

        if not os.path.exists(file_path):
            return False

        try:
            with open(file_path, 'r') as f:
                data = json.load(f)
            self.from_dict(data)
            return True
        except Exception as e:
            print(f"Error loading configuration from {file_path}: {e}")
            return False

    # Class Methods for ClientObj Conversion
    @classmethod
    def client_obj_to_dict(cls, client_obj: ClientObj) -> Dict[str, Any]:
        """
        Convert a ClientObj instance to a dictionary for configuration storage.

        Args:
            client_obj: ClientObj instance to convert

        Returns:
            Dictionary representation of the client
        """
        return {
            "ip_address": client_obj.ip_address,
            "hostname": client_obj.host_name,
            "ports": client_obj.ports,
            "screen_position": client_obj.screen_position,
            "screen_resolution": client_obj.screen_resolution,
            "client_name": client_obj.client_name,
            "ssl": client_obj.ssl,
            "additional_params": client_obj.additional_params
        }

    @classmethod
    def dict_to_client_obj(cls, data: Dict[str, Any]) -> ClientObj:
        """
        Convert a dictionary to a ClientObj instance.

        Args:
            data: Dictionary containing client information

        Returns:
            ClientObj instance created from dictionary
        """
        return ClientObj(
            ip_address=data.get("ip_address"),
            hostname=data.get("hostname"),
            ports=data.get("ports", {}),
            screen_position=data.get("screen_position", "center"),
            screen_resolution=data.get("screen_resolution", "1920x1080"),
            client_name=data.get("client_name", "Unknown"),
            ssl=data.get("ssl", False),
            additional_params=data.get("additional_params", {})
        )

    def load_clients_as_objects(self) -> List[ClientObj]:
        """
        Load ClientObj instances from authorized clients.

        Returns:
            List of ClientObj instances
        """
        clients = []
        for client_data in self.authorized_clients:
            try:
                client_obj = self.dict_to_client_obj(client_data)
                clients.append(client_obj)
            except Exception as e:
                print(f"Error loading client from config: {e}")
        return clients

    def save_clients_from_manager(self, clients_manager) -> None:
        """
        Save clients from ClientsManager to configuration.

        Args:
            clients_manager: ClientsManager instance with clients to save
        """
        self.authorized_clients = []
        for client in clients_manager.get_clients():
            client_dict = self.client_obj_to_dict(client)
            self.add_authorized_client(client_dict)


class ClientConfig:
    """
    Client configuration settings with persistent storage support.
    Manages server connection info, streams, SSL, and logging settings.
    """

    # Default values
    DEFAULT_SERVER_HOST = "127.0.0.1"
    DEFAULT_SERVER_PORT = 5555
    DEFAULT_HEARTBEAT_INTERVAL = 1
    DEFAULT_LOG_LEVEL = Logger.INFO

    def __init__(self, app_config: Optional[ApplicationConfig] = None, config_file: Optional[str] = None):
        """
        Initializes the ClientConfig with default settings.

        Args:
            app_config: The application configuration instance.
            config_file: Path to the configuration file. If None, uses default from app_config.
        """
        self.app_config = app_config or ApplicationConfig()
        self.config_file = config_file or os.path.join(
            self.app_config.get_config_dir(),
            self.app_config.client_config_file
        )

        # Server connection settings
        self.server_host: str = self.DEFAULT_SERVER_HOST
        self.server_port: int = self.DEFAULT_SERVER_PORT
        self.client_hostname: Optional[str] = None
        self.heartbeat_interval: int = self.DEFAULT_HEARTBEAT_INTERVAL
        self.auto_reconnect: bool = True

        # Stream management
        self.streams_enabled: Dict[int, bool] = {}

        # SSL configuration (managed by CertificateManager)
        self.ssl_enabled: bool = False

        # Logging configuration
        self.log_level: int = self.DEFAULT_LOG_LEVEL
        self.log_to_file: bool = False
        self.log_file_path: Optional[str] = None

        # Server info (when acting as server in client mode)
        self.server_info: Optional[Dict[str, Any]] = None

    # Stream Management
    def enable_stream(self, stream_type: int) -> None:
        """Enable a specific stream type"""
        self.streams_enabled[stream_type] = True

    def disable_stream(self, stream_type: int) -> None:
        """Disable a specific stream type"""
        self.streams_enabled[stream_type] = False

    def is_stream_enabled(self, stream_type: int) -> bool:
        """Check if a stream type is enabled"""
        return self.streams_enabled.get(stream_type, False)

    # Connection Configuration
    def set_server_connection(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        hostname: Optional[str] = None,
        heartbeat_interval: Optional[int] = None,
        auto_reconnect: Optional[bool] = None
    ) -> None:
        """Update server connection parameters"""
        if host is not None:
            self.server_host = host
        if port is not None:
            self.server_port = port
        if hostname is not None:
            self.client_hostname = hostname
        if heartbeat_interval is not None:
            self.heartbeat_interval = heartbeat_interval
        if auto_reconnect is not None:
            self.auto_reconnect = auto_reconnect

    # SSL Configuration
    def enable_ssl(self) -> None:
        """Enable SSL (certificates managed by CertificateManager)"""
        self.ssl_enabled = True

    def disable_ssl(self) -> None:
        """Disable SSL"""
        self.ssl_enabled = False

    # Logging Configuration
    def set_logging(
        self,
        level: Optional[int] = None,
        log_to_file: Optional[bool] = None,
        log_file_path: Optional[str] = None
    ) -> None:
        """Configure logging settings"""
        if level is not None:
            self.log_level = level
        if log_to_file is not None:
            self.log_to_file = log_to_file
        if log_file_path is not None:
            self.log_file_path = log_file_path

    # Server Info Management
    def set_server_info(self, server_info: Dict[str, Any]) -> None:
        """Set information about the server (for client mode)"""
        self.server_info = server_info

    def get_server_info(self) -> Optional[Dict[str, Any]]:
        """Get server information"""
        return self.server_info

    # Serialization
    def to_dict(self) -> Dict[str, Any]:
        """Convert configuration to dictionary for serialization"""
        return {
            "server_host": self.server_host,
            "server_port": self.server_port,
            "client_hostname": self.client_hostname,
            "heartbeat_interval": self.heartbeat_interval,
            "auto_reconnect": self.auto_reconnect,
            "streams_enabled": self.streams_enabled,
            "ssl_enabled": self.ssl_enabled,
            "log_level": self.log_level,
            "log_to_file": self.log_to_file,
            "log_file_path": self.log_file_path,
            "server_info": self.server_info
        }

    def from_dict(self, data: Dict[str, Any]) -> None:
        """Load configuration from dictionary"""
        self.server_host = data.get("server_host", self.server_host)
        self.server_port = data.get("server_port", self.server_port)
        self.client_hostname = data.get("client_hostname", self.client_hostname)
        self.heartbeat_interval = data.get("heartbeat_interval", self.heartbeat_interval)
        self.auto_reconnect = data.get("auto_reconnect", self.auto_reconnect)

        # Load streams and convert string keys to int if necessary
        streams = data.get("streams_enabled", {})
        self.streams_enabled = {int(k): v for k, v in streams.items()}

        self.ssl_enabled = data.get("ssl_enabled", self.ssl_enabled)
        self.log_level = data.get("log_level", self.log_level)
        self.log_to_file = data.get("log_to_file", self.log_to_file)
        self.log_file_path = data.get("log_file_path", self.log_file_path)
        self.server_info = data.get("server_info", self.server_info)

    # Persistence
    def save(self, file_path: Optional[str] = None) -> None:
        """
        Save configuration to JSON file.

        Args:
            file_path: Path to save the configuration. Uses self.config_file if None.
        """
        file_path = file_path or self.config_file

        # Ensure directory exists
        dir_path = os.path.dirname(file_path)
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)

        with open(file_path, 'w') as f:
            json.dump(self.to_dict(), f, indent=4)

    def load(self, file_path: Optional[str] = None) -> bool:
        """
        Load configuration from JSON file.

        Args:
            file_path: Path to load the configuration from. Uses self.config_file if None.

        Returns:
            True if loaded successfully, False if file doesn't exist
        """
        file_path = file_path or self.config_file

        if not os.path.exists(file_path):
            return False

        try:
            with open(file_path, 'r') as f:
                data = json.load(f)
            self.from_dict(data)
            return True
        except Exception as e:
            print(f"Error loading configuration from {file_path}: {e}")
            return False





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


