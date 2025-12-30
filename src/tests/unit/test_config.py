"""
Unit tests for the config package.
Tests ApplicationConfig, ServerConfig, and ClientConfig classes.
"""

import json
import os

import pytest

from config import ApplicationConfig, ServerConfig, ClientConfig, ServerInfo
from model.client import ClientObj, ScreenPosition
from utils.logging import Logger


# ============================================================================
# Test ApplicationConfig
# ============================================================================


class TestApplicationConfig:
    """Test ApplicationConfig class."""

    def test_application_config_initialization(self):
        """Test that ApplicationConfig initializes with default values."""
        config = ApplicationConfig()

        assert config.service_name == "PyContinuity"
        assert config.app_name == "PyContinuity"
        assert config.main_path == ""
        assert config.ssl_path == "ssl/"
        assert config.server_config_file == "server_config.json"
        assert config.client_config_file == "client_config.json"
        assert config.config_path == "config/"
        assert config.max_chunk_size == 1024
        assert config.max_delay_tolerance == 0.1

    def test_application_config_post_init(self):
        """Test that __post_init__ sets config_files correctly."""
        config = ApplicationConfig()

        assert "server" in config.config_files
        assert "client" in config.config_files
        assert config.config_files["server"] == "server_config.json"
        assert config.config_files["client"] == "client_config.json"

    def test_set_save_path_creates_directory(self, temp_dir):
        """Test that set_save_path creates directory if it doesn't exist."""
        config = ApplicationConfig()
        new_path = os.path.join(str(temp_dir), "new_config_dir")

        assert not os.path.exists(new_path)

        config.set_save_path(new_path)

        assert os.path.exists(new_path)
        assert config.main_path == new_path

    def test_set_save_path_with_existing_directory(self, temp_dir):
        """Test that set_save_path works with existing directory."""
        config = ApplicationConfig()

        config.set_save_path(str(temp_dir))

        assert config.main_path == str(temp_dir)

    def test_get_save_path(self, temp_dir):
        """Test get_save_path returns correct path."""
        config = ApplicationConfig()
        config.set_save_path(str(temp_dir))

        assert config.get_save_path() == str(temp_dir)

    def test_get_config_dir(self, temp_dir):
        """Test get_config_dir returns correct path."""
        config = ApplicationConfig()
        config.set_save_path(str(temp_dir))

        expected_path = os.path.join(str(temp_dir), "config/")
        assert config.get_config_dir() == expected_path

    def test_get_certificate_path(self, temp_dir):
        """Test get_certificate_path returns correct path."""
        config = ApplicationConfig()
        config.set_save_path(str(temp_dir))

        expected_path = os.path.join(str(temp_dir), "config/", "ssl/")
        assert config.get_certificate_path() == expected_path

    def test_custom_config_values(self):
        """Test ApplicationConfig with custom values."""
        config = ApplicationConfig(
            service_name="CustomService",
            max_chunk_size=2048,
            ssl_path="custom_ssl/",
        )

        assert config.service_name == "CustomService"
        assert config.max_chunk_size == 2048
        assert config.ssl_path == "custom_ssl/"


# ============================================================================
# Test ServerConfig - Initialization and Basic Operations
# ============================================================================


class TestServerConfigInitialization:
    """Test ServerConfig initialization."""

    def test_server_config_default_initialization(self):
        """Test ServerConfig initializes with default values."""
        config = ServerConfig()

        assert config.host == ServerConfig.DEFAULT_HOST
        assert config.port == ServerConfig.DEFAULT_PORT
        assert config.heartbeat_interval == ServerConfig.DEFAULT_HEARTBEAT_INTERVAL
        assert config.ssl_enabled is False
        assert config.log_level == Logger.INFO
        assert config.log_to_file is False
        assert config.log_file_path is None
        assert len(config.streams_enabled) == 0
        assert len(config.get_clients()) == 0

    def test_server_config_with_app_config(self, app_config):
        """Test ServerConfig with custom ApplicationConfig."""
        server_config = ServerConfig(app_config)

        assert server_config.app_config == app_config
        expected_file = os.path.join(
            app_config.get_config_dir(), app_config.server_config_file
        )
        assert server_config.config_file == expected_file

    def test_server_config_with_custom_file_path(self, temp_dir):
        """Test ServerConfig with custom config file path."""
        custom_path = os.path.join(str(temp_dir), "custom_server.json")
        server_config = ServerConfig(config_file=custom_path)

        assert server_config.config_file == custom_path


# ============================================================================
# Test ServerConfig - Connection Management
# ============================================================================


class TestServerConfigConnection:
    """Test ServerConfig connection management."""

    def test_set_connection_params_all(self, server_config):
        """Test setting all connection parameters."""
        server_config.set_connection_params(
            host="192.168.1.1", port=8080, heartbeat_interval=5
        )

        assert server_config.host == "192.168.1.1"
        assert server_config.port == 8080
        assert server_config.heartbeat_interval == 5

    def test_set_connection_params_partial(self, server_config):
        """Test setting partial connection parameters."""
        original_host = server_config.host

        server_config.set_connection_params(port=9999)

        assert server_config.host == original_host
        assert server_config.port == 9999

    def test_set_connection_params_none_values(self, server_config):
        """Test that None values don't change existing config."""
        original_host = server_config.host
        original_port = server_config.port

        server_config.set_connection_params(host=None, port=None)

        assert server_config.host == original_host
        assert server_config.port == original_port


# ============================================================================
# Test ServerConfig - Stream Management
# ============================================================================


class TestServerConfigStreams:
    """Test ServerConfig stream management."""

    def test_enable_stream(self, server_config):
        """Test enabling a stream."""
        server_config.enable_stream(0)

        assert server_config.is_stream_enabled(0) is True

    def test_disable_stream(self, server_config):
        """Test disabling a stream."""
        server_config.enable_stream(1)
        assert server_config.is_stream_enabled(1) is True

        server_config.disable_stream(1)
        assert server_config.is_stream_enabled(1) is False

    def test_is_stream_enabled_non_existent(self, server_config):
        """Test checking non-existent stream returns False."""
        assert server_config.is_stream_enabled(999) is False

    def test_enable_multiple_streams(self, server_config):
        """Test enabling multiple streams."""
        streams = [0, 1, 4, 12]
        for stream in streams:
            server_config.enable_stream(stream)

        for stream in streams:
            assert server_config.is_stream_enabled(stream) is True


# ============================================================================
# Test ServerConfig - SSL Management
# ============================================================================


class TestServerConfigSSL:
    """Test ServerConfig SSL management."""

    def test_enable_ssl(self, server_config):
        """Test enabling SSL."""
        assert server_config.ssl_enabled is False

        server_config.enable_ssl()

        assert server_config.ssl_enabled is True

    def test_disable_ssl(self, server_config):
        """Test disabling SSL."""
        server_config.enable_ssl()
        assert server_config.ssl_enabled is True

        server_config.disable_ssl()

        assert server_config.ssl_enabled is False


# ============================================================================
# Test ServerConfig - Logging Configuration
# ============================================================================


@pytest.mark.anyio
class TestServerConfigLogging:
    """Test ServerConfig logging configuration."""

    def test_set_logging_all_params(self, server_config):
        """Test setting all logging parameters."""
        server_config.set_logging(
            level=Logger.DEBUG, log_to_file=True, log_file_path="/tmp/test.log"
        )

        assert server_config.log_level == Logger.DEBUG
        assert server_config.log_to_file is True
        assert server_config.log_file_path == "/tmp/test.log"

    def test_set_logging_partial(self, server_config):
        """Test setting partial logging parameters."""
        server_config.set_logging(level=Logger.ERROR)

        assert server_config.log_level == Logger.ERROR
        assert server_config.log_to_file is False

    def test_set_logging_none_values(self, server_config):
        """Test that None values don't change existing config."""
        original_level = server_config.log_level

        server_config.set_logging(level=None)

        assert server_config.log_level == original_level


# ============================================================================
# Test ServerConfig - Client Management
# ============================================================================


class TestServerConfigClientManagement:
    """Test ServerConfig client management."""

    def test_add_client_with_object(self, server_config):
        """Test adding a client with ClientObj."""
        client = ClientObj(
            ip_address="192.168.1.100",
            hostname="test-client",
            screen_position="top",
        )

        added_client = server_config.add_client(client=client)

        assert added_client == client
        assert len(server_config.get_clients()) == 1

    def test_add_client_with_params(self, server_config):
        """Test adding a client with parameters."""
        added_client = server_config.add_client(
            ip_address="192.168.1.101", hostname="test-client-2", screen_position="left"
        )

        assert added_client.ip_address == "192.168.1.101"
        assert added_client.host_name == "test-client-2"
        assert added_client.screen_position == "left"

    def test_add_multiple_clients(self, server_config):
        """Test adding multiple clients."""
        server_config.add_client(
            ip_address="192.168.1.100", hostname="client1", screen_position="top"
        )
        server_config.add_client(
            ip_address="192.168.1.101", hostname="client2", screen_position="bottom"
        )

        clients = server_config.get_clients()
        assert len(clients) == 2

    def test_remove_client_by_object(self, server_config):
        """Test removing a client by ClientObj."""
        client = server_config.add_client(
            ip_address="192.168.1.100", hostname="test-client"
        )

        result = server_config.remove_client(client=client)

        assert result is True
        assert len(server_config.get_clients()) == 0

    def test_remove_client_by_params(self, server_config):
        """Test removing a client by parameters."""
        server_config.add_client(
            ip_address="192.168.1.100", hostname="test-client", screen_position="top"
        )

        result = server_config.remove_client(
            ip_address="192.168.1.100", screen_position="top"
        )

        assert result is True
        assert len(server_config.get_clients()) == 0

    def test_remove_non_existent_client(self, server_config):
        """Test removing a non-existent client returns False."""
        result = server_config.remove_client(ip_address="192.168.1.999")

        assert result is False

    def test_get_client_by_ip(self, server_config):
        """Test getting a client by IP address."""
        server_config.add_client(
            ip_address="192.168.1.100", hostname="test-client", screen_position="top"
        )

        client = server_config.get_client(ip_address="192.168.1.100")

        assert client is not None
        assert client.ip_address == "192.168.1.100"

    def test_get_client_by_hostname(self, server_config):
        """Test getting a client by hostname."""
        server_config.add_client(
            ip_address="192.168.1.100", hostname="test-client", screen_position="top"
        )

        client = server_config.get_client(hostname="test-client")

        assert client is not None
        assert client.host_name == "test-client"

    def test_get_client_not_found(self, server_config):
        """Test getting a non-existent client returns None."""
        client = server_config.get_client(ip_address="192.168.1.999")

        assert client is None

    def test_get_clients_empty(self, server_config):
        """Test getting clients from empty list."""
        clients = server_config.get_clients()

        assert isinstance(clients, list)
        assert len(clients) == 0


# ============================================================================
# Test ServerConfig - Serialization
# ============================================================================


class TestServerConfigSerialization:
    """Test ServerConfig serialization."""

    def test_to_dict_default(self, server_config):
        """Test converting default config to dictionary."""
        data = server_config.to_dict()

        assert data["host"] == ServerConfig.DEFAULT_HOST
        assert data["port"] == ServerConfig.DEFAULT_PORT
        assert data["heartbeat_interval"] == ServerConfig.DEFAULT_HEARTBEAT_INTERVAL
        assert data["ssl_enabled"] is False
        assert data["streams_enabled"] == {}
        assert data["authorized_clients"] == []

    def test_to_dict_with_data(self, server_config):
        """Test converting config with data to dictionary."""
        server_config.set_connection_params(host="127.0.0.1", port=8080)
        server_config.enable_stream(0)
        server_config.enable_stream(1)
        server_config.enable_ssl()
        server_config.add_client(ip_address="192.168.1.100", hostname="test-client")

        data = server_config.to_dict()

        assert data["host"] == "127.0.0.1"
        assert data["port"] == 8080
        assert data["streams_enabled"][0] is True
        assert data["streams_enabled"][1] is True
        assert data["ssl_enabled"] is True
        assert len(data["authorized_clients"]) == 1

    def test_from_dict_basic(self, server_config):
        """Test loading config from dictionary."""
        data = {
            "host": "10.0.0.1",
            "port": 9999,
            "heartbeat_interval": 10,
            "streams_enabled": {"0": True, "1": False, "4": True},
            "ssl_enabled": True,
            "log_level": Logger.DEBUG,
        }

        server_config.from_dict(data)

        assert server_config.host == "10.0.0.1"
        assert server_config.port == 9999
        assert server_config.heartbeat_interval == 10
        assert server_config.is_stream_enabled(0) is True
        assert server_config.is_stream_enabled(1) is False
        assert server_config.is_stream_enabled(4) is True
        assert server_config.ssl_enabled is True
        assert server_config.log_level == Logger.DEBUG

    def test_from_dict_with_clients(self, server_config):
        """Test loading config with authorized clients."""
        data = {
            "host": "127.0.0.1",
            "port": 5555,
            "authorized_clients": [
                {
                    "host_name": "client1",
                    "ip_address": "192.168.1.100",
                    "screen_position": "top",
                    "screen_resolution": "1920x1080",
                },
                {
                    "host_name": "client2",
                    "ip_address": "192.168.1.101",
                    "screen_position": "left",
                    "screen_resolution": "2560x1440",
                },
            ],
        }

        server_config.from_dict(data)

        clients = server_config.get_clients()
        assert len(clients) == 2
        assert clients[0].host_name == "client1"
        assert clients[1].host_name == "client2"

    def test_from_dict_string_stream_keys(self, server_config):
        """Test that string keys in streams_enabled are converted to int."""
        data = {"streams_enabled": {"0": True, "1": True, "12": False}}

        server_config.from_dict(data)

        assert server_config.is_stream_enabled(0) is True
        assert server_config.is_stream_enabled(1) is True
        assert server_config.is_stream_enabled(12) is False


# ============================================================================
# Test ServerConfig - Persistence
# ============================================================================


@pytest.mark.anyio
class TestServerConfigPersistence:
    """Test ServerConfig persistence (save/load)."""

    async def test_save_to_file(self, server_config, temp_dir):
        """Test saving config to file."""
        config_file = os.path.join(str(temp_dir), "test_server_config.json")
        server_config.set_connection_params(host="127.0.0.1", port=8080)
        server_config.enable_stream(0)

        await server_config.save(config_file)

        assert os.path.exists(config_file)

        # Verify file content
        with open(config_file, "r") as f:
            data = json.load(f)

        assert data["host"] == "127.0.0.1"
        assert data["port"] == 8080

    async def test_load_from_file(self, server_config_with_test_files):
        """Test loading config from file."""
        loaded = await server_config_with_test_files.load()

        assert loaded is True
        assert server_config_with_test_files.host == "127.0.0.1"
        assert server_config_with_test_files.port == 5555
        assert server_config_with_test_files.is_stream_enabled(0) is True
        assert server_config_with_test_files.is_stream_enabled(1) is True

    async def test_load_non_existent_file(self, server_config, temp_dir):
        """Test loading from non-existent file returns False."""
        config_file = os.path.join(str(temp_dir), "non_existent.json")

        loaded = await server_config.load(config_file)

        assert loaded is False

    def test_sync_load_from_file(self, server_config_with_test_files):
        """Test synchronous loading from file."""
        loaded = server_config_with_test_files.sync_load()

        assert loaded is True
        assert server_config_with_test_files.host == "127.0.0.1"
        assert server_config_with_test_files.port == 5555

    def test_sync_load_non_existent_file(self, server_config, temp_dir):
        """Test synchronous loading from non-existent file returns False."""
        config_file = os.path.join(str(temp_dir), "non_existent.json")

        loaded = server_config.sync_load(config_file)

        assert loaded is False

    async def test_save_creates_directory(self, server_config, temp_dir):
        """Test that save creates directory if it doesn't exist."""
        config_file = os.path.join(str(temp_dir), "nested", "dir", "server_config.json")

        await server_config.save(config_file)

        assert os.path.exists(config_file)

    async def test_save_load_roundtrip(self, server_config, temp_dir):
        """Test saving and loading preserves data."""
        config_file = os.path.join(str(temp_dir), "roundtrip.json")

        # Configure
        server_config.set_connection_params(host="10.0.0.1", port=7777)
        server_config.enable_stream(0)
        server_config.enable_stream(4)
        server_config.enable_ssl()
        server_config.add_client(ip_address="192.168.1.100", hostname="client1")

        # Save
        await server_config.save(config_file)

        # Load into new config
        new_config = ServerConfig()
        await new_config.load(config_file)

        # Verify
        assert new_config.host == "10.0.0.1"
        assert new_config.port == 7777
        assert new_config.is_stream_enabled(0) is True
        assert new_config.is_stream_enabled(4) is True
        assert new_config.ssl_enabled is True
        assert len(new_config.get_clients()) == 1


# ============================================================================
# Test ClientConfig - Initialization and Basic Operations
# ============================================================================


@pytest.mark.anyio
class TestClientConfigInitialization:
    """Test ClientConfig initialization."""

    def test_client_config_default_initialization(self):
        """Test ClientConfig initializes with default values."""
        config = ClientConfig()

        assert config.server_info.host == ClientConfig.DEFAULT_SERVER_HOST
        assert config.server_info.port == ClientConfig.DEFAULT_SERVER_PORT
        assert (
            config.server_info.heartbeat_interval
            == ClientConfig.DEFAULT_HEARTBEAT_INTERVAL
        )
        assert config.client_hostname is None
        assert config.ssl_enabled is False
        assert config.log_level == Logger.INFO
        assert len(config.streams_enabled) == 0

    def test_client_config_with_app_config(self, app_config):
        """Test ClientConfig with custom ApplicationConfig."""
        client_config = ClientConfig(app_config)

        assert client_config.app_config == app_config
        expected_file = os.path.join(
            app_config.get_config_dir(), app_config.client_config_file
        )
        assert client_config.config_file == expected_file

    def test_client_config_server_info(self):
        """Test ClientConfig ServerInfo initialization."""
        config = ClientConfig()

        assert isinstance(config.server_info, ServerInfo)
        assert config.server_info.auto_reconnect is True
        assert config.server_info.ssl is False


# ============================================================================
# Test ClientConfig - Hostname Management
# ============================================================================


@pytest.mark.anyio
class TestClientConfigHostname:
    """Test ClientConfig hostname management."""

    def test_set_hostname(self, client_config):
        """Test setting client hostname."""
        client_config.set_hostname("test-hostname")

        assert client_config.get_hostname() == "test-hostname"

    def test_get_hostname_default(self, client_config):
        """Test getting hostname when not set."""
        assert client_config.get_hostname() is None


# ============================================================================
# Test ClientConfig - Stream Management
# ============================================================================


@pytest.mark.anyio
class TestClientConfigStreams:
    """Test ClientConfig stream management."""

    def test_enable_stream(self, client_config):
        """Test enabling a stream."""
        client_config.enable_stream(1)

        assert client_config.is_stream_enabled(1) is True

    def test_disable_stream(self, client_config):
        """Test disabling a stream."""
        client_config.enable_stream(4)
        assert client_config.is_stream_enabled(4) is True

        client_config.disable_stream(4)
        assert client_config.is_stream_enabled(4) is False

    def test_is_stream_enabled_non_existent(self, client_config):
        """Test checking non-existent stream returns False."""
        assert client_config.is_stream_enabled(999) is False


# ============================================================================
# Test ClientConfig - Server Connection Management
# ============================================================================


@pytest.mark.anyio
class TestClientConfigServerConnection:
    """Test ClientConfig server connection management."""

    def test_set_server_connection_all_params(self, client_config):
        """Test setting all server connection parameters."""
        client_config.set_server_connection(
            host="10.0.0.1",
            port=8080,
            heartbeat_interval=5,
            auto_reconnect=False,
            ssl=True,
            additional_params={"timeout": 30},
        )

        assert client_config.server_info.host == "10.0.0.1"
        assert client_config.server_info.port == 8080
        assert client_config.server_info.heartbeat_interval == 5
        assert client_config.server_info.auto_reconnect is False
        assert client_config.server_info.ssl is True
        assert client_config.server_info.additional_params["timeout"] == 30

    def test_set_server_connection_partial(self, client_config):
        """Test setting partial server connection parameters."""
        original_host = client_config.server_info.host

        client_config.set_server_connection(port=9999)

        assert client_config.server_info.host == original_host
        assert client_config.server_info.port == 9999

    def test_get_server_info(self, client_config):
        """Test getting server info object."""
        server_info = client_config.get_server_info()

        assert isinstance(server_info, ServerInfo)
        assert server_info == client_config.server_info

    def test_get_server_host(self, client_config):
        """Test getting server host."""
        client_config.set_server_connection(host="192.168.1.1")

        assert client_config.get_server_host() == "192.168.1.1"

    def test_get_server_port(self, client_config):
        """Test getting server port."""
        client_config.set_server_connection(port=7777)

        assert client_config.get_server_port() == 7777

    def test_get_heartbeat_interval(self, client_config):
        """Test getting heartbeat interval."""
        client_config.set_server_connection(heartbeat_interval=10)

        assert client_config.get_heartbeat_interval() == 10

    def test_do_auto_reconnect(self, client_config):
        """Test checking auto-reconnect setting."""
        assert client_config.do_auto_reconnect() is True

        client_config.set_server_connection(auto_reconnect=False)

        assert client_config.do_auto_reconnect() is False


# ============================================================================
# Test ClientConfig - SSL Management
# ============================================================================


@pytest.mark.anyio
class TestClientConfigSSL:
    """Test ClientConfig SSL management."""

    def test_enable_ssl(self, client_config):
        """Test enabling SSL."""
        assert client_config.ssl_enabled is False
        assert client_config.server_info.ssl is False

        client_config.enable_ssl()

        assert client_config.ssl_enabled is True
        assert client_config.server_info.ssl is True

    def test_disable_ssl(self, client_config):
        """Test disabling SSL."""
        client_config.enable_ssl()

        client_config.disable_ssl()

        assert client_config.ssl_enabled is False
        assert client_config.server_info.ssl is False


# ============================================================================
# Test ClientConfig - Logging Configuration
# ============================================================================


@pytest.mark.anyio
class TestClientConfigLogging:
    """Test ClientConfig logging configuration."""

    def test_set_logging_all_params(self, client_config):
        """Test setting all logging parameters."""
        client_config.set_logging(
            level=Logger.DEBUG, log_to_file=True, log_file_path="/tmp/client.log"
        )

        assert client_config.log_level == Logger.DEBUG
        assert client_config.log_to_file is True
        assert client_config.log_file_path == "/tmp/client.log"

    def test_set_logging_partial(self, client_config):
        """Test setting partial logging parameters."""
        client_config.set_logging(level=Logger.ERROR)

        assert client_config.log_level == Logger.ERROR
        assert client_config.log_to_file is False


# ============================================================================
# Test ClientConfig - ServerInfo Serialization
# ============================================================================


@pytest.mark.anyio
class TestServerInfoSerialization:
    """Test ServerInfo serialization."""

    def test_server_info_to_dict(self):
        """Test converting ServerInfo to dictionary."""
        server_info = ServerInfo(
            uid="server-123",
            host="10.0.0.1",
            port=8080,
            heartbeat_interval=5,
            auto_reconnect=False,
            ssl=True,
            additional_params={"key": "value"},
        )

        data = server_info.to_dict()

        assert data["uid"] == "server-123"
        assert data["host"] == "10.0.0.1"
        assert data["port"] == 8080
        assert data["heartbeat_interval"] == 5
        assert data["auto_reconnect"] is False
        assert data["ssl"] is True
        assert data["additional_params"]["key"] == "value"

    def test_server_info_from_dict(self):
        """Test creating ServerInfo from dictionary."""
        data = {
            "host": "192.168.1.1",
            "port": 9999,
            "heartbeat_interval": 10,
            "auto_reconnect": False,
            "ssl": True,
            "additional_params": {"timeout": 60},
        }

        server_info = ServerInfo.from_dict(data)

        assert server_info.host == "192.168.1.1"
        assert server_info.port == 9999
        assert server_info.heartbeat_interval == 10
        assert server_info.auto_reconnect is False
        assert server_info.ssl is True
        assert server_info.additional_params["timeout"] == 60

    def test_server_info_from_dict_defaults(self):
        """Test creating ServerInfo from dictionary with missing fields."""
        data = {}

        server_info = ServerInfo.from_dict(data)

        assert server_info.host == "127.0.0.1"
        assert server_info.port == 5555
        assert server_info.heartbeat_interval == 1
        assert server_info.auto_reconnect is True
        assert server_info.ssl is False


# ============================================================================
# Test ClientConfig - Serialization
# ============================================================================


@pytest.mark.anyio
class TestClientConfigSerialization:
    """Test ClientConfig serialization."""

    def test_to_dict_default(self, client_config):
        """Test converting default config to dictionary."""
        data = client_config.to_dict()

        assert "server_info" in data
        assert data["server_info"]["host"] == ClientConfig.DEFAULT_SERVER_HOST
        assert data["server_info"]["port"] == ClientConfig.DEFAULT_SERVER_PORT
        assert data["client_hostname"] is None
        assert data["ssl_enabled"] is False
        assert data["streams_enabled"] == {}

    def test_to_dict_with_data(self, client_config):
        """Test converting config with data to dictionary."""
        client_config.set_hostname("test-client")
        client_config.set_server_connection(host="10.0.0.1", port=8080)
        client_config.enable_stream(1)
        client_config.enable_stream(4)
        client_config.enable_ssl()

        data = client_config.to_dict()

        assert data["client_hostname"] == "test-client"
        assert data["server_info"]["host"] == "10.0.0.1"
        assert data["server_info"]["port"] == 8080
        assert data["streams_enabled"][1] is True
        assert data["streams_enabled"][4] is True
        assert data["ssl_enabled"] is True

    def test_from_dict_basic(self, client_config):
        """Test loading config from dictionary."""
        data = {
            "server_info": {
                "host": "192.168.1.1",
                "port": 9999,
                "heartbeat_interval": 5,
                "auto_reconnect": False,
                "ssl": True,
            },
            "client_hostname": "my-client",
            "streams_enabled": {"1": True, "4": False, "12": True},
            "ssl_enabled": True,
            "log_level": Logger.DEBUG,
        }

        client_config.from_dict(data)

        assert client_config.server_info.host == "192.168.1.1"
        assert client_config.server_info.port == 9999
        assert client_config.client_hostname == "my-client"
        assert client_config.is_stream_enabled(1) is True
        assert client_config.is_stream_enabled(4) is False
        assert client_config.ssl_enabled is True
        assert client_config.log_level == Logger.DEBUG

    def test_from_dict_string_stream_keys(self, client_config):
        """Test that string keys in streams_enabled are converted to int."""
        data = {"streams_enabled": {"1": True, "4": True, "12": False}}

        client_config.from_dict(data)

        assert client_config.is_stream_enabled(1) is True
        assert client_config.is_stream_enabled(4) is True
        assert client_config.is_stream_enabled(12) is False


# ============================================================================
# Test ClientConfig - Persistence
# ============================================================================


@pytest.mark.anyio
class TestClientConfigPersistence:
    """Test ClientConfig persistence (save/load)."""

    async def test_save_to_file(self, client_config, temp_dir):
        """Test saving config to file."""
        config_file = os.path.join(str(temp_dir), "test_client_config.json")
        client_config.set_hostname("test-client")
        client_config.set_server_connection(host="10.0.0.1", port=8080)
        client_config.enable_stream(1)

        await client_config.save(config_file)

        assert os.path.exists(config_file)

        # Verify file content
        with open(config_file, "r") as f:
            data = json.load(f)

        assert data["client_hostname"] == "test-client"
        assert data["server_info"]["host"] == "10.0.0.1"

    async def test_load_from_file(self, client_config_with_test_files):
        """Test loading config from file."""
        loaded = await client_config_with_test_files.load()

        assert loaded is True
        assert client_config_with_test_files.client_hostname == "test-client-hostname"
        assert client_config_with_test_files.server_info.host == "localhost"
        assert client_config_with_test_files.server_info.port == 5555
        assert client_config_with_test_files.is_stream_enabled(1) is True

    async def test_load_non_existent_file(self, client_config, temp_dir):
        """Test loading from non-existent file returns False."""
        config_file = os.path.join(str(temp_dir), "non_existent.json")

        loaded = await client_config.load(config_file)

        assert loaded is False

    def test_sync_load_from_file(self, client_config_with_test_files):
        """Test synchronous loading from file."""
        loaded = client_config_with_test_files.sync_load()

        assert loaded is True
        assert client_config_with_test_files.client_hostname == "test-client-hostname"

    def test_sync_load_non_existent_file(self, client_config, temp_dir):
        """Test synchronous loading from non-existent file returns False."""
        config_file = os.path.join(str(temp_dir), "non_existent.json")

        loaded = client_config.sync_load(config_file)

        assert loaded is False

    async def test_save_creates_directory(self, client_config, temp_dir):
        """Test that save creates directory if it doesn't exist."""
        config_file = os.path.join(str(temp_dir), "nested", "dir", "client_config.json")

        await client_config.save(config_file)

        assert os.path.exists(config_file)

    async def test_save_load_roundtrip(self, client_config, temp_dir):
        """Test saving and loading preserves data."""
        config_file = os.path.join(str(temp_dir), "roundtrip.json")

        # Configure
        client_config.set_hostname("roundtrip-client")
        client_config.set_server_connection(
            host="10.0.0.1", port=7777, auto_reconnect=False
        )
        client_config.enable_stream(1)
        client_config.enable_stream(12)
        client_config.enable_ssl()

        # Save
        await client_config.save(config_file)

        # Load into new config
        new_config = ClientConfig()
        await new_config.load(config_file)

        # Verify
        assert new_config.client_hostname == "roundtrip-client"
        assert new_config.server_info.host == "10.0.0.1"
        assert new_config.server_info.port == 7777
        assert new_config.server_info.auto_reconnect is False
        assert new_config.is_stream_enabled(1) is True
        assert new_config.is_stream_enabled(12) is True
        assert new_config.ssl_enabled is True


# ============================================================================
# Test Edge Cases and Error Handling
# ============================================================================


@pytest.mark.anyio
class TestConfigEdgeCases:
    """Test edge cases and error handling."""

    async def test_load_corrupted_json_file(self, server_config, temp_dir):
        """Test loading from corrupted JSON file."""
        config_file = os.path.join(str(temp_dir), "corrupted.json")

        # Create corrupted file
        with open(config_file, "w") as f:
            f.write("{invalid json content")

        loaded = await server_config.load(config_file)

        assert loaded is False

    def test_server_config_invalid_client_data(self, server_config):
        """Test loading server config with invalid client data."""
        data = {
            "authorized_clients": [
                {
                    "host_name": "valid-client",
                    "ip_address": "WRONG_IP_FORMAT",
                    "screen_position": "top",
                },
            ]
        }

        # Should not crash, just skip invalid client
        server_config.from_dict(data)

        # Only valid client should be loaded
        clients = server_config.get_clients()
        assert len(clients) == 1
        assert clients[0].host_name == "valid-client"

    def test_empty_streams_enabled(self, server_config):
        """Test config with empty streams_enabled dictionary."""
        data = {"streams_enabled": {}}

        server_config.from_dict(data)

        assert len(server_config.streams_enabled) == 0

    async def test_save_with_permission_error(self, server_config):
        """Test save handles permission errors gracefully."""
        # Try to save to a likely protected location
        # This test may behave differently on different systems
        protected_path = "/root/config.json" if os.name != "nt" else "C:\\config.json"

        try:
            await server_config.save(protected_path)
        except (PermissionError, OSError):
            # Expected behavior - permission denied
            pass
