"""
Unified configuration management system for PyContinuity.
Handles server and client configurations with persistent storage support.
"""

import asyncio
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
import json
import os

import aiofiles

from model.client import ClientObj, ClientsManager
from utils.logging import Logger


@dataclass
class ApplicationConfig:
    """Application-wide configuration settings"""

    service_name: str = "PyContinuity"
    app_name: str = "PyContinuity"
    main_path: str = ""  # Main application save path

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

    DEFAULT_HOST: str = "0.0.0.0"
    DEFAULT_PORT: int = 55655

    config_files: dict = field(default_factory=dict)

    version: str = "1.0.0"

    def __post_init__(self):
        self.config_files = {
            "server": self.server_config_file,
            "client": self.client_config_file,
        }

    def set_save_path(self, path: str) -> None:
        # Validate and set the main application save path
        if not os.path.exists(path):
            os.makedirs(path, exist_ok=True)
        self.main_path = path

    def get_save_path(self) -> str:
        return self.main_path

    def get_config_dir(self) -> str:
        return os.path.join(self.get_save_path(), self.config_path)

    def get_certificate_path(self) -> str:
        return os.path.join(self.get_config_dir(), self.ssl_path)


class ServerConfig:
    """
    Server configuration settings with persistent storage support.
    Manages streams, SSL, connection settings, logging, and authorized clients.
    """

    # Default values
    DEFAULT_HOST = ApplicationConfig.DEFAULT_HOST
    DEFAULT_PORT = ApplicationConfig.DEFAULT_PORT
    DEFAULT_HEARTBEAT_INTERVAL: int = 1
    DEFAULT_LOG_LEVEL: int = Logger.INFO

    def __init__(
        self,
        app_config: Optional[ApplicationConfig] = None,
        config_file: Optional[str] = None,
    ):
        """
        Initializes the ServerConfig with default settings.

        Args:
            app_config: The application configuration instance.
            config_file: Path to the configuration file. If None, uses default from app_config.
        """
        self.app_config = app_config or ApplicationConfig()
        self.config_file = config_file or os.path.join(
            self.app_config.get_config_dir(), self.app_config.server_config_file
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

        # Authorized clients managed by ClientsManager
        self.clients_manager: ClientsManager = ClientsManager()

        # Legacy storage for serialization
        self.authorized_clients: List[Dict[str, Any]] = []

        # UID
        self.uid: Optional[str] = None

        self._write_lock = asyncio.Lock()

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
        heartbeat_interval: Optional[int] = None,
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
        log_file_path: Optional[str] = None,
    ) -> None:
        """Configure logging settings"""
        if level is not None:
            self.log_level = level
        if log_to_file is not None:
            self.log_to_file = log_to_file
        if log_file_path is not None:
            self.log_file_path = log_file_path

    # Client Management through ClientsManager
    def add_client(
        self,
        client: Optional[ClientObj] = None,
        ip_address: Optional[str] = None,
        hostname: Optional[str] = None,
        screen_position: str = "top",
    ) -> ClientObj:
        """
        Add a client to the authorized list.

        Args:
            client: ClientObj instance to add (if provided, other params are ignored)
            ip_address: IP address of the client
            hostname: Hostname of the client
            screen_position: Screen position relative to server

        Returns:
            The ClientObj instance that was added
        """
        if client is None:
            client = ClientObj(
                ip_address=ip_address,
                hostname=hostname,
                screen_position=screen_position,
            )

        self.clients_manager.add_client(client)
        return client

    def remove_client(
        self,
        client: Optional[ClientObj] = None,
        ip_address: Optional[str] = None,
        hostname: Optional[str] = None,
        screen_position: Optional[str] = None,
    ) -> bool:
        """
        Remove a client from the authorized list.

        Args:
            client: ClientObj instance to remove
            ip_address: IP address of the client
            hostname: Hostname of the client
            screen_position: Screen position of the client

        Returns:
            True if client was removed, False if not found
        """
        if client is None:
            client = self.clients_manager.get_client(
                ip_address=ip_address,
                hostname=hostname,
                screen_position=screen_position,
            )

        if client:
            self.clients_manager.remove_client(client)
            return True
        return False

    def get_client(
        self,
        ip_address: Optional[str] = None,
        hostname: Optional[str] = None,
        screen_position: Optional[str] = None,
    ) -> Optional[ClientObj]:
        """
        Get a specific client.

        Args:
            ip_address: IP address of the client
            hostname: Hostname of the client
            screen_position: Screen position of the client

        Returns:
            ClientObj if found, None otherwise
        """
        return self.clients_manager.get_client(
            ip_address=ip_address, hostname=hostname, screen_position=screen_position
        )

    def get_clients(self) -> List[ClientObj]:
        """
        Get all authorized clients as ClientObj instances.

        Returns:
            List of ClientObj instances
        """
        return self.clients_manager.get_clients()

    # Serialization
    def to_dict(self) -> Dict[str, Any]:
        """Convert configuration to dictionary for serialization"""
        # Sync clients from ClientsManager to authorized_clients list
        self.authorized_clients = [
            client.to_dict() for client in self.clients_manager.get_clients()
        ]

        return {
            "uid": self.uid,
            "host": self.host,
            "port": self.port,
            "heartbeat_interval": self.heartbeat_interval,
            "streams_enabled": self.streams_enabled,
            "ssl_enabled": self.ssl_enabled,
            "log_level": self.log_level,
            "log_to_file": self.log_to_file,
            "log_file_path": self.log_file_path,
            "authorized_clients": self.authorized_clients,
        }

    def from_dict(self, data: Dict[str, Any]) -> None:
        """Load configuration from dictionary"""
        self.uid = data.get("uid", self.uid)
        self.host = data.get("host", self.host)
        self.port = data.get("port", self.port)
        self.heartbeat_interval = data.get(
            "heartbeat_interval", self.heartbeat_interval
        )

        # Load streams and convert string keys to int if necessary
        streams = data.get("streams_enabled", {})
        self.streams_enabled = {int(k): v for k, v in streams.items()}

        self.ssl_enabled = data.get("ssl_enabled", self.ssl_enabled)
        self.log_level = data.get("log_level", self.log_level)
        self.log_to_file = data.get("log_to_file", self.log_to_file)
        self.log_file_path = data.get("log_file_path", self.log_file_path)

        # Load authorized clients into ClientsManager
        self.authorized_clients = data.get("authorized_clients", [])
        self.clients_manager = ClientsManager()  # Reset manager
        for client_data in self.authorized_clients:
            try:
                client_obj = ClientObj.from_dict(client_data)
                self.clients_manager.add_client(client_obj)
            except Exception as e:
                print(f"Error loading client from config: {e}")

    @staticmethod
    async def _write(file, content: str) -> None:
        with open(file, "w") as f:
            for line in content:
                f.write(line)
                await asyncio.sleep(0)

    # Persistence
    async def save(self, file_path: Optional[str] = None) -> None:
        """
        Save configuration to JSON file.

        Args:
            file_path: Path to save the configuration. Uses self.config_file if None.
        """
        async with self._write_lock:
            file_path = file_path or self.config_file

            # Ensure directory exists
            dir_path = os.path.dirname(file_path)
            if dir_path:
                os.makedirs(dir_path, exist_ok=True)

            try:
                config_data = self.to_dict()
                json_content = json.dumps(config_data, indent=4)
            except Exception as e:
                raise ValueError(f"Failed to serialize configuration ({e})")

            if not config_data or not json_content.strip():
                raise ValueError("Configuration data is empty, aborting save")

            temp_file = f"{file_path}.tmp"
            try:
                await self._write(temp_file, json_content)

                # Rinomina atomicamente (sovrascrive il file originale)
                os.replace(temp_file, file_path)
            except Exception as e:
                # Rimuovi il file temporaneo in caso di errore
                if os.path.exists(temp_file):
                    os.remove(temp_file)
                raise IOError(f"Failed to save configuration: {e}")

    def sync_load(self, file_path: Optional[str] = None) -> bool:
        """
        Synchronous wrapper for loading configuration from JSON file.

        Args:
            file_path: Path to load the configuration from. Uses self.config_file if None.
        Returns:
            True if loaded successfully, False if file doesn't exist
        """
        file_path = file_path or self.config_file

        if not os.path.exists(file_path):
            return False

        try:
            with open(file_path, "r") as f:
                data = json.load(f)
            self.from_dict(data)
            return True
        except Exception as e:
            print(f"Error loading configuration from {file_path}: {e}")
            return False

    async def load(self, file_path: Optional[str] = None) -> bool:
        """
        Load configuration from JSON file.

        Args:
            file_path: Path to load the configuration from. Uses self.config_file if None.

        Returns:
            True if loaded successfully, False if file doesn't exist
        """
        file_path = file_path or self.config_file

        if not os.path.exists(file_path):
            # print cwd
            print(f"Current working directory: {os.getcwd()}")
            return False

        try:
            async with aiofiles.open(file_path, "r") as f:
                content = await f.read()
                data = json.loads(content)
            self.from_dict(data)
            return True
        except Exception as e:
            print(f"Error loading configuration from {file_path}: {e}")
            return False


class ClientConfig:
    """
    Client configuration settings with persistent storage support.
    Manages server connection info, streams, SSL, and logging settings.
    Includes a ServerInfo class for unified server connection management.
    """

    # Default values
    DEFAULT_UID: str = ""
    DEFAULT_SERVER_HOST = ApplicationConfig.DEFAULT_HOST
    DEFAULT_SERVER_PORT = ApplicationConfig.DEFAULT_PORT
    DEFAULT_HEARTBEAT_INTERVAL: int = 1
    DEFAULT_LOG_LEVEL: int = Logger.INFO

    def __init__(
        self,
        app_config: Optional[ApplicationConfig] = None,
        config_file: Optional[str] = None,
    ):
        """
        Initializes the ClientConfig with default settings.

        Args:
            app_config: The application configuration instance.
            config_file: Path to the configuration file. If None, uses default from app_config.
        """
        self.app_config = app_config or ApplicationConfig()
        self.config_file = config_file or os.path.join(
            self.app_config.get_config_dir(), self.app_config.client_config_file
        )

        # Server connection information
        self.server_info = ServerInfo(
            uid=self.DEFAULT_UID,
            host=self.DEFAULT_SERVER_HOST,
            port=self.DEFAULT_SERVER_PORT,
            heartbeat_interval=self.DEFAULT_HEARTBEAT_INTERVAL,
        )

        # Client-specific settings
        self.client_hostname: Optional[str] = None
        self.uid: Optional[str] = None

        # Stream management
        self.streams_enabled: Dict[int, bool] = {}

        # SSL configuration (managed by CertificateManager)
        self.ssl_enabled: bool = False

        # Logging configuration
        self.log_level: int = self.DEFAULT_LOG_LEVEL
        self.log_to_file: bool = False
        self.log_file_path: Optional[str] = None

        self._write_lock = asyncio.Lock()

    def get_uid(self) -> Optional[str]:
        """Get the client UID"""
        return self.uid

    def set_uid(self, uid: str) -> None:
        """Set the client UID"""
        self.uid = uid

    def set_hostname(self, hostname: str) -> None:
        """Set the client hostname"""
        self.client_hostname = hostname

    def get_hostname(self) -> Optional[str]:
        """Get the client hostname"""
        return self.client_hostname

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

    # Server Connection Management
    def get_server_info(self) -> "ServerInfo":
        """Get server connection information"""
        return self.server_info

    def set_server_connection(
        self,
        uid: Optional[str] = None,
        host: Optional[str] = None,
        hostname: Optional[str] = None,
        port: Optional[int] = None,
        heartbeat_interval: Optional[int] = None,
        auto_reconnect: Optional[bool] = None,
        ssl: Optional[bool] = None,
        additional_params: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Update server connection parameters"""
        if uid is not None:
            self.server_info.uid = uid
        if host is not None:
            self.server_info.host = host
        if hostname is not None:
            self.server_info.hostname = hostname
        if port is not None:
            self.server_info.port = port
        if heartbeat_interval is not None:
            self.server_info.heartbeat_interval = heartbeat_interval
        if auto_reconnect is not None:
            self.server_info.auto_reconnect = auto_reconnect
        if ssl is not None:
            self.server_info.ssl = ssl
        if additional_params is not None:
            self.server_info.additional_params.update(additional_params)

    def get_server_uid(self) -> str:
        """Get server UID"""
        return self.server_info.uid

    def get_server_host(self) -> str:
        """Get server host"""
        return self.server_info.host

    def get_server_hostname(self) -> Optional[str]:
        """Get server hostname"""
        return self.server_info.hostname

    def get_server_port(self) -> int:
        """Get server port"""
        return self.server_info.port

    def get_heartbeat_interval(self) -> int:
        """Get heartbeat interval"""
        return self.server_info.heartbeat_interval

    def do_auto_reconnect(self) -> bool:
        """Check if auto-reconnect is enabled"""
        return self.server_info.auto_reconnect

    # SSL Configuration
    def enable_ssl(self) -> None:
        """Enable SSL (certificates managed by CertificateManager)"""
        self.ssl_enabled = True
        self.server_info.ssl = True

    def disable_ssl(self) -> None:
        """Disable SSL"""
        self.ssl_enabled = False
        self.server_info.ssl = False

    # Logging Configuration
    def set_logging(
        self,
        level: Optional[int] = None,
        log_to_file: Optional[bool] = None,
        log_file_path: Optional[str] = None,
    ) -> None:
        """Configure logging settings"""
        if level is not None:
            self.log_level = level
        if log_to_file is not None:
            self.log_to_file = log_to_file
        if log_file_path is not None:
            self.log_file_path = log_file_path

    # Serialization
    def to_dict(self) -> Dict[str, Any]:
        """Convert configuration to dictionary for serialization"""
        return {
            "server_info": self.server_info.to_dict(),
            "uid": self.uid,
            "client_hostname": self.client_hostname,
            "streams_enabled": self.streams_enabled,
            "ssl_enabled": self.ssl_enabled,
            "log_level": self.log_level,
            "log_to_file": self.log_to_file,
            "log_file_path": self.log_file_path,
        }

    def from_dict(self, data: Dict[str, Any]) -> None:
        """Load configuration from dictionary"""
        # Load server info
        if "server_info" in data:
            self.server_info = ServerInfo.from_dict(data["server_info"])

        # old format
        # elif "server_host" in data or "server_port" in data:
        #     self.server_info = self.ServerInfo(
        #         host=data.get("server_host", self.DEFAULT_SERVER_HOST),
        #         port=data.get("server_port", self.DEFAULT_SERVER_PORT),
        #         heartbeat_interval=data.get("heartbeat_interval", self.DEFAULT_HEARTBEAT_INTERVAL),
        #         auto_reconnect=data.get("auto_reconnect", True),
        #         ssl=data.get("ssl_enabled", False)
        #     )

        self.uid = data.get("uid")
        self.client_hostname = data.get("client_hostname", self.client_hostname)

        # Load streams and convert string keys to int if necessary
        streams = data.get("streams_enabled", {})
        self.streams_enabled = {int(k): v for k, v in streams.items()}

        self.ssl_enabled = data.get("ssl_enabled", self.ssl_enabled)
        self.log_level = data.get("log_level", self.log_level)
        self.log_to_file = data.get("log_to_file", self.log_to_file)
        self.log_file_path = data.get("log_file_path", self.log_file_path)

    @staticmethod
    async def _write(file, content: str) -> None:
        with open(file, "w") as f:
            for line in content:
                f.write(line)
                await asyncio.sleep(0)

    # Persistence
    async def save(self, file_path: Optional[str] = None) -> None:
        """
        Save configuration to JSON file.

        Args:
            file_path: Path to save the configuration. Uses self.config_file if None.
        """
        async with self._write_lock:
            file_path = file_path or self.config_file

            # Ensure directory exists
            dir_path = os.path.dirname(file_path)
            if dir_path:
                os.makedirs(dir_path, exist_ok=True)

            try:
                config_data = self.to_dict()
                json_content = json.dumps(config_data, indent=4)
            except Exception as e:
                raise ValueError(f"Failed to serialize configuration ({e})")

            if not config_data or not json_content.strip():
                raise ValueError("Configuration data is empty, aborting save")

            temp_file = f"{file_path}.tmp"
            try:
                await self._write(temp_file, json_content)

                # Rinomina atomicamente (sovrascrive il file originale)
                os.replace(temp_file, file_path)
            except Exception as e:
                # Rimuovi il file temporaneo in caso di errore
                if os.path.exists(temp_file):
                    os.remove(temp_file)
                raise IOError(f"Failed to save configuration: {e}")

    def sync_load(self, file_path: Optional[str] = None) -> bool:
        """
        Synchronous wrapper for loading configuration from JSON file.

        Args:
            file_path: Path to load the configuration from. Uses self.config_file if None.
        Returns:
            True if loaded successfully, False if file doesn't exist
        """
        file_path = file_path or self.config_file

        if not os.path.exists(file_path):
            return False

        try:
            with open(file_path, "r") as f:
                data = json.load(f)
            self.from_dict(data)
            return True
        except Exception as e:
            print(f"Error loading configuration from {file_path}: {e}")
            return False

    async def load(self, file_path: Optional[str] = None) -> bool:
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
            async with aiofiles.open(file_path, "r") as f:
                content = await f.read()
                data = json.loads(content)
            self.from_dict(data)
            return True
        except Exception as e:
            print(f"Error loading configuration from {file_path}: {e}")
            return False


class ServerInfo:
    """
    Manages server connection information for the client.
    """

    def __init__(
        self,
        uid: str,
        host: str = ClientConfig.DEFAULT_SERVER_HOST,
        hostname: Optional[str] = None,
        port: int = ClientConfig.DEFAULT_SERVER_PORT,
        heartbeat_interval: int = ClientConfig.DEFAULT_HEARTBEAT_INTERVAL,
        auto_reconnect: bool = True,
        ssl: bool = False,
        additional_params: Optional[Dict[str, Any]] = None,
    ):
        self.uid = uid
        self.host = host
        self.hostname = hostname
        self.port = port
        self.heartbeat_interval = heartbeat_interval
        self.auto_reconnect = auto_reconnect
        self.ssl = ssl
        self.additional_params = additional_params or {}

    def to_dict(self) -> Dict[str, Any]:
        """Convert ServerInfo to dictionary"""
        return {
            "uid": self.uid,
            "host": self.host,
            "hostname": self.hostname,
            "port": self.port,
            "heartbeat_interval": self.heartbeat_interval,
            "auto_reconnect": self.auto_reconnect,
            "ssl": self.ssl,
            "additional_params": self.additional_params,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ServerInfo":
        """Create ServerInfo from dictionary"""
        return cls(
            uid=data.get("uid", ""),
            host=data.get("host", ClientConfig.DEFAULT_SERVER_HOST),
            hostname=data.get("hostname"),
            port=data.get("port", ClientConfig.DEFAULT_SERVER_PORT),
            heartbeat_interval=data.get("heartbeat_interval", 1),
            auto_reconnect=data.get("auto_reconnect", True),
            ssl=data.get("ssl", False),
            additional_params=data.get("additional_params", {}),
        )
