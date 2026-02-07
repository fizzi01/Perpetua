"""
Unit tests for the config package.
Tests ApplicationConfig, ServerConfig, and ClientConfig classes.
"""


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

import msgspec.json
import os

import pytest

from config import ApplicationConfig, ServerConfig, ClientConfig, ServerInfo

_encoder = msgspec.json.Encoder()
_decoder = msgspec.json.Decoder()


# ============================================================================
# Test ApplicationConfig
# ============================================================================


class TestApplicationConfig:
    """Test ApplicationConfig class."""

    def test_application_config_initialization(self):
        """Test that ApplicationConfig initializes with default values."""
        config = ApplicationConfig()

        assert config.service_name == "Perpetua"
        assert config.app_name == "Perpetua"
        assert config.main_path != ""
        assert config.ssl_path == "ssl/"
        assert config.config_path == "config/"
        assert config.max_chunk_size == 1024
        assert config.max_delay_tolerance == 0.1

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


# ============================================================================
# Test ServerConfig - Initialization and Basic Operations
# ============================================================================


class TestServerConfigInitialization:
    """Test ServerConfig initialization."""

    def test_server_config_with_app_config(self, app_config):
        """Test ServerConfig with custom ApplicationConfig."""
        server_config = ServerConfig(app_config)

        assert server_config.app_config == app_config


# ============================================================================
# Test ServerConfig - Connection Management
# ============================================================================


class TestServerConfigConnection:
    """Test ServerConfig connection management."""

    def test_set_connection_params(self, server_config):
        """Test setting connection parameters."""
        server_config.set_connection_params(
            host="192.168.1.1", port=8080, heartbeat_interval=5
        )

        assert server_config.host == "192.168.1.1"
        assert server_config.port == 8080
        assert server_config.heartbeat_interval == 5


# ============================================================================
# Test ServerConfig - Stream Management
# ============================================================================


class TestServerConfigStreams:
    """Test ServerConfig stream management."""

    def test_enable_and_disable_stream(self, server_config):
        """Test enabling and disabling streams."""
        server_config.enable_stream(0)
        assert server_config.is_stream_enabled(0) is True

        server_config.disable_stream(0)
        assert server_config.is_stream_enabled(0) is False

    def test_stream_not_enabled_by_default(self, server_config):
        """Test that streams are not enabled by default."""
        assert server_config.is_stream_enabled(999) is False


# ============================================================================
# Test ServerConfig - SSL Management
# ============================================================================


class TestServerConfigSSL:
    """Test ServerConfig SSL management."""

    def test_enable_and_disable_ssl(self, server_config):
        """Test enabling and disabling SSL."""
        server_config.enable_ssl()
        assert server_config.ssl_enabled is True

        server_config.disable_ssl()
        assert server_config.ssl_enabled is False


# ============================================================================
# Test ServerConfig - Client Management
# ============================================================================


class TestServerConfigClientManagement:
    """Test ServerConfig client management."""

    def test_add_and_remove_client(self, server_config):
        """Test adding and removing clients."""
        client = server_config.add_client(
            ip_address="192.168.1.100", hostname="test-client", screen_position="top"
        )
        assert len(server_config.get_clients()) == 1

        result = server_config.remove_client(client=client)
        assert result is True
        assert len(server_config.get_clients()) == 0

    def test_get_client_by_ip(self, server_config):
        """Test getting a client by IP address."""
        server_config.add_client(
            ip_address="192.168.1.100", hostname="test-client", screen_position="top"
        )

        client = server_config.get_client(ip_address="192.168.1.100")
        assert client is not None
        assert client.ip_address == "192.168.1.100"

    def test_get_client_not_found(self, server_config):
        """Test getting a non-existent client returns None."""
        client = server_config.get_client(ip_address="192.168.1.999")
        assert client is None

    def test_remove_non_existent_client(self, server_config):
        """Test removing a non-existent client returns False."""
        result = server_config.remove_client(ip_address="192.168.1.999")
        assert result is False


# ============================================================================
# Test ServerConfig - Serialization
# ============================================================================


class TestServerConfigSerialization:
    """Test ServerConfig serialization."""

    def test_to_dict_roundtrip(self, server_config):
        """Test to_dict and from_dict roundtrip."""
        server_config.set_connection_params(host="127.0.0.1", port=8080)
        server_config.enable_stream(0)
        server_config.enable_ssl()

        # Convert to dict
        data = server_config.to_dict()

        # Load into new config
        new_config = ServerConfig()
        new_config.from_dict(data)

        # Verify
        assert new_config.host == "127.0.0.1"
        assert new_config.port == 8080
        assert new_config.is_stream_enabled(0) is True
        assert new_config.ssl_enabled is True

    def test_from_dict_with_string_stream_keys(self, server_config):
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

    async def test_save_and_load_roundtrip(self, server_config, temp_dir):
        """Test saving and loading preserves data."""
        config_file = os.path.join(str(temp_dir), "roundtrip.json")

        # Configure
        server_config.set_connection_params(host="10.0.0.1", port=7777)
        server_config.enable_stream(0)
        server_config.enable_ssl()

        # Save
        await server_config.save(config_file)
        assert os.path.exists(config_file)

        # Load into new config
        new_config = ServerConfig()
        loaded = await new_config.load(config_file)
        assert loaded is True

        # Verify
        assert new_config.host == "10.0.0.1"
        assert new_config.port == 7777
        assert new_config.is_stream_enabled(0) is True
        assert new_config.ssl_enabled is True

    async def test_load_non_existent_file(self, server_config, temp_dir):
        """Test loading from non-existent file returns False."""
        config_file = os.path.join(str(temp_dir), "non_existent.json")
        loaded = await server_config.load(config_file)
        assert loaded is False

    def test_sync_load(self, app_config_with_test_files, server_config_with_test_files):
        """Test synchronous loading from file."""
        loaded = server_config_with_test_files.sync_load()
        assert loaded is True


# ============================================================================
# Test ClientConfig - Hostname Management
# ============================================================================


@pytest.mark.anyio
class TestClientConfigHostname:
    """Test ClientConfig hostname management."""

    def test_set_and_get_hostname(self, client_config):
        """Test setting and getting client hostname."""
        client_config.set_hostname("test-hostname")
        assert client_config.get_hostname() == "test-hostname"


# ============================================================================
# Test ClientConfig - Stream Management
# ============================================================================


@pytest.mark.anyio
class TestClientConfigStreams:
    """Test ClientConfig stream management."""

    def test_enable_and_disable_stream(self, client_config):
        """Test enabling and disabling streams."""
        client_config.enable_stream(1)
        assert client_config.is_stream_enabled(1) is True

        client_config.disable_stream(1)
        assert client_config.is_stream_enabled(1) is False


# ============================================================================
# Test ClientConfig - Server Connection Management
# ============================================================================


@pytest.mark.anyio
class TestClientConfigServerConnection:
    """Test ClientConfig server connection management."""

    def test_set_server_connection(self, client_config):
        """Test setting server connection parameters."""
        client_config.set_server_connection(
            host="10.0.0.1",
            port=8080,
            heartbeat_interval=5,
            auto_reconnect=False,
        )

        assert client_config.get_server_host() == "10.0.0.1"
        assert client_config.get_server_port() == 8080
        assert client_config.get_heartbeat_interval() == 5
        assert client_config.do_auto_reconnect() is False


# ============================================================================
# Test ClientConfig - SSL Management
# ============================================================================


@pytest.mark.anyio
class TestClientConfigSSL:
    """Test ClientConfig SSL management."""

    def test_enable_and_disable_ssl(self, client_config):
        """Test enabling and disabling SSL."""
        client_config.enable_ssl()
        assert client_config.ssl_enabled is True
        assert client_config.server_info.ssl is True

        client_config.disable_ssl()
        assert client_config.ssl_enabled is False
        assert client_config.server_info.ssl is False


# ============================================================================
# Test ClientConfig - ServerInfo Serialization
# ============================================================================


@pytest.mark.anyio
class TestServerInfoSerialization:
    """Test ServerInfo serialization."""

    def test_server_info_roundtrip(self):
        """Test ServerInfo to_dict and from_dict roundtrip."""
        server_info = ServerInfo(
            uid="server-123",
            host="10.0.0.1",
            port=8080,
            heartbeat_interval=5,
            auto_reconnect=False,
        )

        data = server_info.to_dict()
        new_info = ServerInfo.from_dict(data)

        assert new_info.uid == "server-123"
        assert new_info.host == "10.0.0.1"
        assert new_info.port == 8080
        assert new_info.heartbeat_interval == 5
        assert new_info.auto_reconnect is False


# ============================================================================
# Test ClientConfig - Serialization
# ============================================================================


@pytest.mark.anyio
class TestClientConfigSerialization:
    """Test ClientConfig serialization."""

    def test_to_dict_and_from_dict(self, client_config):
        """Test to_dict and from_dict roundtrip."""
        client_config.set_hostname("test-client")
        client_config.set_server_connection(host="10.0.0.1", port=8080)
        client_config.enable_stream(1)

        # Convert to dict
        data = client_config.to_dict()

        # Load into new config
        new_config = ClientConfig()
        new_config.from_dict(data)

        # Verify
        assert new_config.get_hostname() == "test-client"
        assert new_config.get_server_host() == "10.0.0.1"
        assert new_config.get_server_port() == 8080
        assert new_config.is_stream_enabled(1) is True

    def test_from_dict_with_string_stream_keys(self, client_config):
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

    async def test_save_and_load_roundtrip(self, client_config, temp_dir):
        """Test saving and loading preserves data."""
        config_file = os.path.join(str(temp_dir), "roundtrip.json")

        # Configure
        client_config.set_hostname("roundtrip-client")
        client_config.set_server_connection(host="10.0.0.1", port=8080)
        client_config.enable_stream(1)

        # Save
        await client_config.save(config_file)
        assert os.path.exists(config_file)

        # Load into new config
        new_config = ClientConfig()
        loaded = await new_config.load(config_file)
        assert loaded is True

        # Verify
        assert new_config.get_hostname() == "roundtrip-client"
        assert new_config.get_server_host() == "10.0.0.1"
        assert new_config.get_server_port() == 8080
        assert new_config.is_stream_enabled(1) is True

    async def test_load_non_existent_file(self, client_config, temp_dir):
        """Test loading from non-existent file returns False."""
        config_file = os.path.join(str(temp_dir), "non_existent.json")
        loaded = await client_config.load(config_file)
        assert loaded is False

    def test_sync_load(self, app_config_with_test_files, client_config_with_test_files):
        """Test synchronous loading from file."""
        loaded = client_config_with_test_files.sync_load()
        assert loaded is True
