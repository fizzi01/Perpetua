"""
Unit tests for ServiceDiscovery class.
"""

import asyncio
import hashlib
import time
from typing import List
from unittest.mock import AsyncMock, MagicMock, Mock, patch, call

import pytest

from service import Service, ServiceDiscovery
from zeroconf import ServiceInfo, Zeroconf
from zeroconf.asyncio import AsyncZeroconf, AsyncServiceBrowser


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def mock_async_zeroconf():
    """Provide a mocked AsyncZeroconf instance."""
    mock = AsyncMock(spec=AsyncZeroconf)
    mock.async_register_service = AsyncMock()
    mock.async_unregister_all_services = AsyncMock()
    mock.zeroconf = AsyncMock(spec=Zeroconf)
    return mock


@pytest.fixture
def service_discovery():
    """Provide a ServiceDiscovery instance without mDNS."""
    d = ServiceDiscovery(timeout=1.0)
    d._async_zercnf = None  # Explicitly set to None
    return d


@pytest.fixture
async def service_discovery_with_mock(mock_async_zeroconf):
    """Provide a ServiceDiscovery instance with mocked AsyncZeroconf."""
    d = ServiceDiscovery(async_mdns=mock_async_zeroconf, timeout=1.0)
    yield d
    # Cleanup after test
    await d.unregister_service()


@pytest.fixture
def sample_services():
    """Provide sample services for testing."""
    return [
        Service(
            name="service1._pycontinuity._tcp.local.",
            address="192.168.1.100",
            port=8000,
            uid="uid1",
            hostname="host1.local",
        ),
        Service(
            name="service2._pycontinuity._tcp.local.",
            address="192.168.1.101",
            port=8001,
            uid="uid2",
            hostname="host2.local",
        ),
        Service(
            name="service3._pycontinuity._tcp.local.",
            address="192.168.1.102",
            port=8002,
            uid="uid3",
            hostname="host3.local",
        ),
    ]


@pytest.fixture
def mock_service_info():
    """Provide a mocked ServiceInfo."""
    mock_info = Mock(spec=ServiceInfo)
    mock_info.parsed_addresses.return_value = ["192.168.1.100"]
    mock_info.port = 8000
    mock_info.properties = {b"hostname": b"test-hostname.local"}
    return mock_info

@pytest.fixture
def mock_async_service_info():
    """Provide a mocked AsyncServiceInfo."""
    mock_info = AsyncMock(spec=ServiceInfo)
    mock_info.async_request = AsyncMock()
    mock_info.parsed_addresses.return_value = ["192.168.1.100"]
    mock_info.port = 8000
    mock_info.properties = {b"hostname": b"test-hostname.local"}
    return mock_info


# ============================================================================
# Service Class Tests
# ============================================================================


class TestService:
    """Test the Service class."""

    def test_service_creation(self):
        """Test creating a Service instance."""
        service = Service(
            name="test_service",
            address="192.168.1.100",
            port=8000,
            uid="test_uid",
            hostname="test-host.local",
        )

        assert service.name == "test_service"
        assert service.address == "192.168.1.100"
        assert service.port == 8000
        assert service.uid == "test_uid"
        assert service.hostname == "test-host.local"

    def test_service_creation_without_port(self):
        """Test creating a Service instance without port."""
        service = Service(name="test_service", address="192.168.1.100")

        assert service.name == "test_service"
        assert service.address == "192.168.1.100"
        assert service.port is None
        assert service.uid is None
        assert service.hostname is None


# ============================================================================
# ServiceDiscovery Initialization Tests
# ============================================================================


class TestServiceDiscoveryInit:
    """Test ServiceDiscovery initialization."""

    def test_init_default_values(self):
        """Test initialization with default values."""
        sd = ServiceDiscovery()

        assert isinstance(sd._async_zercnf, AsyncZeroconf)
        assert sd._mdns_timeout == 5.0
        assert sd._uid is None
        assert "_tcp.local." in sd._service_type
        assert sd._service_type.startswith("_")

    def test_init_with_async_zeroconf(self, mock_async_zeroconf):
        """Test initialization with AsyncZeroconf instance."""
        sd = ServiceDiscovery(async_mdns=mock_async_zeroconf, timeout=2.0)

        assert sd._async_zercnf is mock_async_zeroconf
        assert sd._mdns_timeout == 2.0

    def test_init_with_custom_timeout(self):
        """Test initialization with custom timeout."""
        sd = ServiceDiscovery(timeout=10.0)

        assert sd._mdns_timeout == 10.0


# ============================================================================
# UID Generation Tests
# ============================================================================


class TestUIDGeneration:
    """Test UID generation functionality."""

    def test_generate_uid_static_method(self):
        """Test _generate_uid static method."""
        host = "192.168.1.100"

        with patch("time.time", return_value=1234567890.0):
            uid = ServiceDiscovery.generate_uid(host)

            # Verify UID is generated correctly
            expected_string = f"{host}-{1234567890.0}"
            expected_hash = hashlib.sha256(expected_string.encode()).hexdigest()[
                : ServiceDiscovery.UID_LEN
            ]

            assert uid == expected_hash
            assert len(uid) == ServiceDiscovery.UID_LEN

    def test_generate_uid_different_hosts(self):
        """Test that different hosts generate different UIDs."""
        with patch("time.time", return_value=1234567890.0):
            uid1 = ServiceDiscovery.generate_uid("192.168.1.100")
            uid2 = ServiceDiscovery.generate_uid("192.168.1.101")

            assert uid1 != uid2

    def test_generate_uid_different_times(self):
        """Test that different times generate different UIDs."""
        host = "192.168.1.100"

        with patch("time.time", return_value=1234567890.0):
            uid1 = ServiceDiscovery.generate_uid(host)

        with patch("time.time", return_value=1234567891.0):
            uid2 = ServiceDiscovery.generate_uid(host)

            assert uid1 != uid2

    def test_generate_uid_deterministic(self):
        """Test that UID generation is deterministic with same inputs."""
        host = "192.168.1.100"
        timestamp = 1234567890.0

        with patch("time.time", return_value=timestamp):
            uid1 = ServiceDiscovery.generate_uid(host)
            uid2 = ServiceDiscovery.generate_uid(host)

            assert uid1 == uid2

    def test_generate_uid_exception_handling(self):
        """Test exception handling in _generate_uid."""
        with patch("hashlib.sha256", side_effect=Exception("Hash error")):
            with pytest.raises(RuntimeError, match="Failed to generate UID"):
                ServiceDiscovery.generate_uid("192.168.1.100")

    def test_get_uid_initially_none(self, service_discovery):
        """Test that get_uid returns None initially."""
        assert service_discovery.get_uid() is None

    @pytest.mark.anyio
    async def test_get_uid_after_registration(self, service_discovery_with_mock):
        """Test that get_uid returns UID after service registration."""
        await service_discovery_with_mock.register_service("192.168.1.100", 8000)

        uid = service_discovery_with_mock.get_uid()
        assert uid is not None
        assert isinstance(uid, str)
        assert len(uid) == ServiceDiscovery.UID_LEN


# ============================================================================
# Hostname Resolution Tests
# ============================================================================


class TestHostnameResolution:
    """Test hostname resolution and IP validation functionality."""

    def test_is_ip_valid_ipv4(self):
        """Test _is_ip with valid IPv4 addresses."""
        assert ServiceDiscovery._is_ip("192.168.1.100") is True
        assert ServiceDiscovery._is_ip("127.0.0.1") is True
        assert ServiceDiscovery._is_ip("10.0.0.1") is True
        assert ServiceDiscovery._is_ip("8.8.8.8") is True

    def test_is_ip_valid_ipv6(self):
        """Test _is_ip with valid IPv6 addresses."""
        assert ServiceDiscovery._is_ip("::1") is True
        assert (
            ServiceDiscovery._is_ip("2001:0db8:85a3:0000:0000:8a2e:0370:7334") is True
        )
        assert ServiceDiscovery._is_ip("fe80::1") is True

    def test_is_ip_invalid(self):
        """Test _is_ip with invalid IP addresses."""
        assert ServiceDiscovery._is_ip("hostname.local") is False
        assert ServiceDiscovery._is_ip("test-host") is False
        assert ServiceDiscovery._is_ip("256.256.256.256") is False
        assert ServiceDiscovery._is_ip("not.an.ip.address") is False
        assert ServiceDiscovery._is_ip("") is False

    @pytest.mark.anyio
    async def test_resolve_hostname_success(self):
        """Test successful hostname resolution."""
        hostname = "localhost"

    @pytest.mark.anyio
    async def test_resolve_hostname_success(self):
        """Test successful hostname resolution."""
        hostname = "localhost"

        with patch("service.get_local_ip", return_value="127.0.0.1") as mock_get_local_ip:
            ip = await ServiceDiscovery.resolve_hostname(hostname)
            assert ip == "127.0.0.1"
            mock_get_local_ip.assert_called_once()

    @pytest.mark.anyio
    async def test_resolve_hostname_failure(self):
        """Test hostname resolution failure."""
        hostname = "invalid-nonexistent-host.local"

        with patch("service.get_local_ip", side_effect=Exception("Name resolution failed")):
            with pytest.raises(
                    RuntimeError, match=f"Failed to resolve hostname {hostname}"
            ):
                await ServiceDiscovery.resolve_hostname(hostname)


# ============================================================================
# Service Registration Tests
# ============================================================================


class TestServiceRegistration:
    """Test service registration functionality."""

    @pytest.mark.anyio
    async def test_register_service_success(
        self, service_discovery_with_mock, mock_async_zeroconf
    ):
        """Test successful service registration."""
        host = "192.168.1.100"
        port = 8000

        await service_discovery_with_mock.register_service(host, port, uid=None)

        # Verify async_register_service was called
        mock_async_zeroconf.async_register_service.assert_called_once()

        # Verify UID was generated
        assert service_discovery_with_mock.get_uid() is not None

    @pytest.mark.anyio
    async def test_register_service_empty_host(self, service_discovery_with_mock):
        """Test registration with empty host raises ValueError."""
        with pytest.raises(ValueError, match="Host cannot be an empty string"):
            await service_discovery_with_mock._register_service("", 8000)

    @pytest.mark.anyio
    async def test_register_service_no_zeroconf(self, service_discovery):
        """Test registration without AsyncZeroconf raises RuntimeError."""
        with pytest.raises(RuntimeError, match="Zeroconf instance is not initialized"):
            await service_discovery._register_service("192.168.1.100", 8000)

    @pytest.mark.anyio
    async def test_register_service_generates_uid(self, service_discovery_with_mock):
        """Test that registration generates a UID."""
        host = "192.168.1.100"
        port = 8000

        with patch("time.time", return_value=1234567890.0):
            await service_discovery_with_mock.register_service(host, port, uid=None)

            uid = service_discovery_with_mock.get_uid()
            assert uid is not None

            # Verify UID format
            expected_string = f"{host}-{1234567890.0}"
            expected_hash = hashlib.sha256(expected_string.encode()).hexdigest()[
                : ServiceDiscovery.UID_LEN
            ]
            assert uid == expected_hash

    @pytest.mark.anyio
    async def test_register_service_creates_correct_service_info(
        self, service_discovery_with_mock, mock_async_zeroconf
    ):
        """Test that registration creates ServiceInfo with correct parameters."""
        host = "192.168.1.100"
        port = 8000

        await service_discovery_with_mock.register_service(host, port, uid=None)

        # Get the call arguments
        call_args = mock_async_zeroconf.async_register_service.call_args
        service_info = call_args[0][0]

        # Verify ServiceInfo properties
        assert isinstance(service_info, ServiceInfo)
        assert service_info.port == port
        assert host in service_info.parsed_addresses()

    @pytest.mark.anyio
    async def test_register_service_exception_handling(
        self, service_discovery_with_mock, mock_async_zeroconf
    ):
        """Test exception handling during registration."""
        mock_async_zeroconf.async_register_service.side_effect = Exception(
            "Registration failed"
        )

        with pytest.raises(RuntimeError, match="Failed to register mDNS service"):
            await service_discovery_with_mock.register_service(
                "192.168.1.100", 8000, uid=None
            )

    @pytest.mark.anyio
    async def test_register_service_multiple_times_same_uid(
        self, service_discovery_with_mock
    ):
        """Test that registering multiple times uses the same UID."""
        host = "192.168.1.100"

        await service_discovery_with_mock.register_service(host, 8000, uid=None)
        uid1 = service_discovery_with_mock.get_uid()

        await service_discovery_with_mock.register_service(host, 8001, uid=None)
        uid2 = service_discovery_with_mock.get_uid()

        assert uid1 == uid2

    @pytest.mark.anyio
    async def test_register_service_with_custom_uid(self, service_discovery_with_mock):
        """Test registering service with a custom UID."""
        host = "192.168.1.100"
        port = 8000
        custom_uid = "custom_uid_12345"

        await service_discovery_with_mock.register_service(host, port, uid=custom_uid)

        # Verify the custom UID was used
        uid = service_discovery_with_mock.get_uid()
        assert uid == custom_uid

    @pytest.mark.anyio
    async def test_register_service_with_hostname(
        self, service_discovery_with_mock, mock_async_zeroconf
    ):
        """Test registering service with hostname instead of IP."""
        hostname = "test-host.local"
        port = 8000

        # Mock hostname resolution
        with patch.object(
            ServiceDiscovery, "resolve_hostname", return_value="192.168.1.100"
        ) as mock_resolve:
            await service_discovery_with_mock.register_service(hostname, port, uid=None)

            # Verify hostname was resolved
            mock_resolve.assert_called_once_with(hostname)

            # Verify service was registered
            mock_async_zeroconf.async_register_service.assert_called_once()

            # Verify the ServiceInfo contains hostname in properties
            call_args = mock_async_zeroconf.async_register_service.call_args
            service_info: ServiceInfo = call_args[0][0]
            assert service_info.properties.get(b"hostname") == hostname.encode("utf-8")

    @pytest.mark.anyio
    async def test_register_service_hostname_resolution_failure(
        self, service_discovery_with_mock
    ):
        """Test registration with hostname that fails to resolve."""
        hostname = "invalid-host.local"
        port = 8000

        # Mock hostname resolution to raise exception
        with patch.object(
            ServiceDiscovery,
            "resolve_hostname",
            side_effect=RuntimeError("Failed to resolve"),
        ):
            with pytest.raises(RuntimeError, match="Failed to resolve"):
                await service_discovery_with_mock.register_service(
                    hostname, port, uid=None
                )


# ============================================================================
# Service Unregistration Tests
# ============================================================================


class TestServiceUnregistration:
    """Test service unregistration functionality."""

    @pytest.mark.anyio
    async def test_unregister_service_success(
        self, service_discovery_with_mock, mock_async_zeroconf
    ):
        """Test successful service unregistration."""
        await service_discovery_with_mock.unregister_service()

        # Verify async_unregister_all_services was called
        mock_async_zeroconf.async_unregister_all_services.assert_called_once()

    @pytest.mark.anyio
    async def test_unregister_service_no_zeroconf(self, service_discovery):
        """Test unregistration without AsyncZeroconf raises RuntimeError."""
        with pytest.raises(RuntimeError, match="Zeroconf instance is not initialized"):
            await service_discovery._unregister_service()

    @pytest.mark.anyio
    async def test_unregister_service_after_registration(
        self, service_discovery_with_mock, mock_async_zeroconf
    ):
        """Test unregistering after registration."""
        # Register first
        await service_discovery_with_mock.register_service(
            "192.168.1.100", 8000, uid=None
        )

        # Then unregister
        await service_discovery_with_mock.unregister_service()

        # Verify both methods were called
        mock_async_zeroconf.async_register_service.assert_called_once()
        mock_async_zeroconf.async_unregister_all_services.assert_called_once()


# ============================================================================
# Service Discovery Tests
# ============================================================================


class TestServiceDiscoveryMethods:
    """Test service discovery functionality."""

    @pytest.mark.anyio
    async def test_discover_services_no_services(self, service_discovery_with_mock):
        """Test discovering services when none are available."""
        with (
            patch("service._ServiceListener") as mock_listener_class,
            patch("service.AsyncServiceBrowser") as mock_browser_class,
        ):
            # Setup mocks
            mock_listener = Mock()
            mock_listener.get_services.return_value = []
            mock_listener_class.return_value = mock_listener

            mock_browser = AsyncMock()
            mock_browser.async_cancel = AsyncMock()
            mock_browser_class.return_value = mock_browser

            # Discover services
            services = await service_discovery_with_mock.discover_services()

            # Verify results
            assert services == []
            mock_browser.async_cancel.assert_called_once()
            service_discovery_with_mock._async_zercnf.zeroconf.close.assert_called_once()

    @pytest.mark.anyio
    async def test_discover_services_with_services(
        self, service_discovery, sample_services
    ):
        """Test discovering services when services are available."""
        with (
            patch("service._ServiceListener") as mock_listener_class,
            patch("service.Zeroconf") as mock_zeroconf_class,
            patch("service.AsyncServiceBrowser") as mock_browser_class,
        ):
            # Setup mocks
            mock_listener = Mock()
            mock_listener.get_services.return_value = sample_services
            mock_listener_class.return_value = mock_listener

            mock_zeroconf = Mock()
            mock_zeroconf.close = Mock()
            mock_zeroconf_class.return_value = mock_zeroconf
            service_discovery._async_zercnf = AsyncMock()
            service_discovery._async_zercnf.zeroconf = mock_zeroconf

            mock_browser = AsyncMock()
            mock_browser.async_cancel = AsyncMock()
            mock_browser_class.return_value = mock_browser

            # Discover services
            services = await service_discovery.discover_services()

            # Verify results
            assert len(services) == 3
            assert services == sample_services
            mock_browser.async_cancel.assert_called_once()
            mock_zeroconf.close.assert_called_once()

    @pytest.mark.anyio
    async def test_discover_services_timeout(self, service_discovery):
        """Test that discovery respects the timeout."""
        custom_timeout = 2.0
        service_discovery._mdns_timeout = custom_timeout

        with (
            patch("service._ServiceListener") as mock_listener_class,
            patch("service.Zeroconf") as mock_zeroconf_class,
            patch("service.AsyncServiceBrowser") as mock_browser_class,
            patch("asyncio.sleep") as mock_sleep,
        ):
            # Setup mocks
            mock_listener = Mock()
            mock_listener.get_services.return_value = []
            mock_listener_class.return_value = mock_listener

            mock_zeroconf = Mock()
            mock_zeroconf.close = Mock()
            mock_zeroconf_class.return_value = mock_zeroconf
            service_discovery._async_zercnf = AsyncMock()
            service_discovery._async_zercnf.zeroconf = mock_zeroconf

            mock_browser = AsyncMock()
            mock_browser.async_cancel = AsyncMock()
            mock_browser_class.return_value = mock_browser

            mock_sleep.return_value = None

            # Discover services
            await service_discovery.discover_services()

            # Verify timeout was used
            mock_sleep.assert_called_once_with(custom_timeout)

    @pytest.mark.anyio
    async def test_discover_services_exception_handling(self, service_discovery):
        """Test exception handling during discovery."""
        with patch(
            "service._ServiceListener", side_effect=Exception("Discovery failed")
        ):
            with pytest.raises(
                RuntimeError, match=r"^Failed to start mDNS service discovery"
            ):
                service_discovery._async_zercnf = AsyncMock()
                service_discovery._async_zercnf.zeroconf = Mock()
                await service_discovery._discover_services()

    @pytest.mark.anyio
    async def test_discover_services_browser_cancel_called(self, service_discovery):
        """Test that browser.async_cancel is called after discovery."""
        with (
            patch("service._ServiceListener") as mock_listener_class,
            patch("service.Zeroconf") as mock_zeroconf_class,
            patch("service.AsyncServiceBrowser") as mock_browser_class,
        ):
            # Setup mocks
            mock_listener = Mock()
            mock_listener.get_services.return_value = []
            mock_listener_class.return_value = mock_listener

            mock_zeroconf = Mock()
            mock_zeroconf.close = Mock()
            mock_zeroconf_class.return_value = mock_zeroconf
            service_discovery._async_zercnf = Mock()
            service_discovery._async_zercnf.zeroconf = mock_zeroconf

            mock_browser = AsyncMock()
            mock_browser.async_cancel = AsyncMock()
            mock_browser_class.return_value = mock_browser

            # Discover services
            await service_discovery.discover_services()

            # Verify browser was cancelled
            mock_browser.async_cancel.assert_called_once()

    @pytest.mark.anyio
    async def test_discover_services_zeroconf_closed(self, service_discovery):
        """Test that Zeroconf is closed after discovery."""
        with (
            patch("service._ServiceListener") as mock_listener_class,
            patch("service.Zeroconf") as mock_zeroconf_class,
            patch("service.AsyncServiceBrowser") as mock_browser_class,
        ):
            # Setup mocks
            mock_listener = Mock()
            mock_listener.get_services.return_value = []
            mock_listener_class.return_value = mock_listener

            mock_zeroconf = Mock()
            mock_zeroconf.close = Mock()
            mock_zeroconf_class.return_value = mock_zeroconf
            service_discovery._async_zercnf = Mock()
            service_discovery._async_zercnf.zeroconf = mock_zeroconf

            mock_browser = AsyncMock()
            mock_browser.async_cancel = AsyncMock()
            mock_browser_class.return_value = mock_browser

            # Discover services
            await service_discovery.discover_services()

            # Verify Zeroconf was closed
            mock_zeroconf.close.assert_called_once()


# ============================================================================
# _ServiceListener Tests
# ============================================================================


class TestServiceListener:
    """Test the _ServiceListener class."""

    def test_listener_initialization(self):
        """Test _ServiceListener initialization."""
        from service import _ServiceListener

        listener = _ServiceListener()
        assert listener.get_services() == []

    @pytest.mark.anyio
    async def test_listener_add_service(self, mock_async_service_info):
        """Test adding a service to the listener."""
        from service import _ServiceListener

        listener = _ServiceListener()

        # Create mock Zeroconf
        mock_zeroconf = Mock(spec=Zeroconf)

        # Mock AsyncServiceInfo
        with patch("service.AsyncServiceInfo", return_value=mock_async_service_info):
            # Add service
            service_type = "_pycontinuity._tcp.local."
            service_name = "test_uid._pycontinuity._tcp.local."
            listener.add_service(mock_zeroconf, service_type, service_name)

            # Wait for the async task to complete
            await asyncio.sleep(0.1)

            # Verify service was added
            services = listener.get_services()
            assert len(services) == 1
            assert services[0].name == service_name
            assert services[0].address == "192.168.1.100"
            assert services[0].port == 8000
            assert services[0].uid == "test_uid"
            assert services[0].hostname == "test-hostname.local"

    @pytest.mark.anyio
    async def test_listener_add_service_no_info(self, mock_async_service_info):
        """Test adding a service when no info is available."""
        from service import _ServiceListener

        listener = _ServiceListener()

        # Create mock Zeroconf
        mock_zeroconf = Mock(spec=Zeroconf)
        mock_async_service_info.parsed_addresses.return_value = []
        # Mock AsyncServiceInfo to return None after async_request
        with patch("service.AsyncServiceInfo", return_value=mock_async_service_info):
            # Add service
            service_type = "_pycontinuity._tcp.local."
            service_name = "test._pycontinuity._tcp.local."
            listener.add_service(mock_zeroconf, service_type, service_name)

            await asyncio.sleep(0.1)

            # Verify no service was added
            services = listener.get_services()
            assert len(services) == 0

    @pytest.mark.anyio
    async def test_listener_add_multiple_services(self, mock_async_service_info):
        """Test adding multiple services."""
        from service import _ServiceListener
        from service import AsyncServiceInfo

        listener = _ServiceListener()

        # Create mock Zeroconf
        mock_zeroconf = Mock(spec=Zeroconf)

        with patch("service.AsyncServiceInfo") as mock_async_info_class:
            # Create first service info
            mock_async_info1 = Mock()
            mock_async_info1.async_request = AsyncMock()
            mock_async_info1.parsed_addresses.return_value = ["192.168.1.100"]
            mock_async_info1.port = 8000
            mock_async_info1.properties = {b"hostname": b"host1.local"}

            # Create second service info
            mock_async_info2 = Mock()
            mock_async_info2.async_request = AsyncMock()
            mock_async_info2.parsed_addresses.return_value = ["192.168.1.101"]
            mock_async_info2.port = 8001
            mock_async_info2.properties = {b"hostname": b"host2.local"}

            mock_async_info_class.side_effect = [mock_async_info1, mock_async_info2]

            # Add services
            service_type = "_pycontinuity._tcp.local."
            listener.add_service(
                mock_zeroconf, service_type, "uid1._pycontinuity._tcp.local."
            )
            listener.add_service(
                mock_zeroconf, service_type, "uid2._pycontinuity._tcp.local."
            )

            await asyncio.sleep(0.2)

            # Verify services were added
            services = listener.get_services()
            assert len(services) == 2
            assert services[0].address == "192.168.1.100"
            assert services[0].uid == "uid1"
            assert services[0].hostname == "host1.local"
            assert services[1].address == "192.168.1.101"
            assert services[1].uid == "uid2"
            assert services[1].hostname == "host2.local"

    @pytest.mark.anyio
    @pytest.mark.anyio
    async def test_listener_update_service(self):
        """Test updating an existing service."""
        from service import _ServiceListener

        listener = _ServiceListener()

        # Create mock Zeroconf
        mock_zeroconf = Mock(spec=Zeroconf)

        # Add initial service first
        with patch("service.AsyncServiceInfo") as mock_async_info_class:
            info1 = AsyncMock()
            info1.async_request = AsyncMock()
            info1.parsed_addresses = Mock(return_value=["192.168.1.100"])
            info1.port = 8000
            info1.properties = {b"hostname": b"host1.local"}

            mock_async_info_class.return_value = info1

            service_type = "_pycontinuity._tcp.local."
            service_name = "test_uid._pycontinuity._tcp.local."

            listener.add_service(mock_zeroconf, service_type, service_name)
            await asyncio.sleep(0.2)

        # Verify initial service was added
        services = listener.get_services()
        assert len(services) == 1
        assert services[0].address == "192.168.1.100"

        # Now update the service using get_service_info (sync method)
        info2 = Mock(spec=ServiceInfo)
        info2.parsed_addresses = Mock(return_value=["192.168.1.200"])
        info2.port = 9000
        info2.properties = {b"hostname": b"updated-host.local"}

        mock_zeroconf.get_service_info.return_value = info2
        listener.update_service(mock_zeroconf, service_type, service_name)

        # Verify service was updated
        services = listener.get_services()
        assert len(services) == 1
        assert services[0].address == "192.168.1.200"
        assert services[0].port == 9000
        assert services[0].hostname == "updated-host.local"

    def test_listener_update_nonexistent_service(self):
        """Test updating a service that doesn't exist."""
        from service import _ServiceListener

        listener = _ServiceListener()

        # Create mock Zeroconf
        mock_zeroconf = Mock(spec=Zeroconf)

        info = Mock(spec=ServiceInfo)
        info.parsed_addresses.return_value = ["192.168.1.100"]
        info.port = 8000
        info.properties = {}

        mock_zeroconf.get_service_info.return_value = info

        # Update non-existent service (should not crash)
        service_type = "_pycontinuity._tcp.local."
        listener.update_service(mock_zeroconf, service_type, "nonexistent")

        # Verify no services were added
        services = listener.get_services()
        assert len(services) == 0

    @pytest.mark.anyio
    async def test_listener_add_service_without_hostname(self):
        """Test adding a service without hostname property."""
        from service import _ServiceListener

        listener = _ServiceListener()

        # Create mock Zeroconf
        mock_zeroconf = AsyncMock(spec=Zeroconf)
        with patch("service.AsyncServiceInfo") as mock_async_info_class:
            # Create service info without hostname
            mock_async_info = AsyncMock()
            mock_async_info.async_request = AsyncMock()
            mock_async_info.parsed_addresses.return_value = ["192.168.1.100"]
            mock_async_info.port = 8000
            mock_async_info.properties = {}  # No hostname property

            mock_async_info_class.return_value = mock_async_info

            mock_zeroconf.get_service_info.return_value = mock_async_info_class

            # Add service
            service_type = "_pycontinuity._tcp.local."
            service_name = "test_uid._pycontinuity._tcp.local."
            listener.add_service(mock_zeroconf, service_type, service_name)
            await asyncio.sleep(0.1)
            # Verify service was added without hostname
            services = listener.get_services()
            assert len(services) == 1
            assert services[0].hostname is None
            assert services[0].address == "192.168.1.100"
            assert services[0].port == 8000
            assert services[0].uid == "test_uid"


# ============================================================================
# Integration Tests
# ============================================================================


class TestServiceDiscoveryIntegration:
    """Integration tests for ServiceDiscovery."""

    @pytest.mark.anyio
    async def test_full_registration_flow(self, mock_async_zeroconf):
        """Test full registration and unregistration flow."""
        sd = ServiceDiscovery(async_mdns=mock_async_zeroconf, timeout=1.0)

        # Register service
        host = "192.168.1.100"
        port = 8000
        await sd.register_service(host, port, uid=None)

        # Verify UID was generated
        uid = sd.get_uid()
        assert uid is not None

        # Verify registration was called
        mock_async_zeroconf.async_register_service.assert_called_once()

        # Unregister service
        await sd.unregister_service()

        # Verify unregistration was called
        mock_async_zeroconf.async_unregister_all_services.assert_called_once()

    @pytest.mark.anyio
    async def test_discovery_returns_service_list(
        self, service_discovery, sample_services
    ):
        """Test that discovery returns a list of services."""
        with (
            patch("service._ServiceListener") as mock_listener_class,
            patch("service.Zeroconf") as mock_zeroconf_class,
            patch("service.AsyncServiceBrowser") as mock_browser_class,
        ):
            # Setup mocks
            mock_listener = Mock()
            mock_listener.get_services.return_value = sample_services
            mock_listener_class.return_value = mock_listener

            mock_zeroconf = Mock()
            mock_zeroconf.close = Mock()
            mock_zeroconf_class.return_value = mock_zeroconf
            service_discovery._async_zercnf = Mock()
            service_discovery._async_zercnf.zeroconf = mock_zeroconf

            mock_browser = AsyncMock()
            mock_browser.async_cancel = AsyncMock()
            mock_browser_class.return_value = mock_browser

            # Discover services
            discovered = await service_discovery.discover_services()

            # Verify all services were discovered
            assert len(discovered) == len(sample_services)
            for i, service in enumerate(discovered):
                assert service.name == sample_services[i].name
                assert service.address == sample_services[i].address
                assert service.port == sample_services[i].port
