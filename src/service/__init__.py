"""
Service package provides server and client public APIs.
"""

from utils import UIDGenerator

import asyncio
from typing import Optional
import socket

from zeroconf import ServiceInfo, ServiceListener, Zeroconf, BadTypeInNameException
from zeroconf.asyncio import AsyncZeroconf, AsyncServiceBrowser, AsyncServiceInfo

from config import ApplicationConfig
from utils.logging import get_logger
from utils.net import get_local_ip


class Service:
    """
    It represents a discovered service on the network.
    """

    def __init__(
        self,
        name: str,
        address: str,
        port: Optional[int] = None,
        hostname: Optional[str] = None,
        uid: Optional[str] = None,
    ):
        """
        An mDNS service instance.

        Args:
            name: Service name.
            address: Service IP address.
            port: Service port.
            hostname: Service hostname.
            uid: Service unique identifier.
        """
        self.uid = uid
        self.name = name
        self.address = address
        self.hostname: Optional[str] = hostname
        self.port = port

    def as_dict(self) -> dict:
        """
        It returns the service as a dictionary.
        """
        return {
            "uid": self.uid,
            # "name": self.name,
            "address": self.address,
            "hostname": self.hostname,
            "port": self.port,
        }


class _ServiceListener(ServiceListener):
    """
    It listens for service updates on the network.
    """

    def __init__(self):
        self._services: list[Service] = []

        self._pending_task: set[asyncio.Task] = set()

    def get_services(self) -> list[Service]:
        """
        It returns the list of discovered services.
        """
        return self._services

    async def _service_info_task(self, zc: Zeroconf, type_: str, name: str):
        # Get service info
        info = AsyncServiceInfo(type_=type_, name=name)
        await info.async_request(zc=zc, timeout=3000)
        if info is not None and len(info.parsed_addresses()) > 0:
            address = info.parsed_addresses()[0]
            uid = name.split(".")[0]
            hostname = info.properties.get(b"hostname", None)
            if hostname is not None:
                hostname = hostname.decode()

            service = Service(name, address, info.port, uid=uid, hostname=hostname)

            self._services.append(service)

    def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        """
        It adds a new service to the list of discovered services.
        """
        task = asyncio.create_task(self._service_info_task(zc, type_, name))
        self._pending_task.add(task)
        task.add_done_callback(self._pending_task.discard)

    async def _service_info_update_task(self, zc: Zeroconf, type_: str, name: str):
        info = AsyncServiceInfo(type_=type_, name=name)
        await info.async_request(zc=zc, timeout=3000)
        if info is not None and len(info.parsed_addresses()) > 0:
            uid = name.split(".")[0]
            for service in self._services:
                if service.uid == uid:
                    service.address = info.parsed_addresses()[0]
                    service.port = info.port
                    b_hostname = info.properties.get(b"hostname", None)
                    if b_hostname is not None:
                        service.hostname = b_hostname.decode()
                    break

    def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        """Update existing service"""
        task = asyncio.create_task(self._service_info_update_task(zc, type_, name))
        self._pending_task.add(task)
        task.add_done_callback(self._pending_task.discard)

    def clear(self):
        """Clear discovered services"""
        # Close pending tasks
        for task in self._pending_task:
            task.cancel()
        self._pending_task.clear()


class ServiceDiscovery:
    """
    It handles service discovery functionalities. It register a server service mDNS.
    It let a client discover servers on the network automatically.
    """

    UID_LEN = 48

    def __init__(
        self, async_mdns: Optional[AsyncZeroconf] = None, timeout: float = 5.0
    ):
        """
        Args:
            async_mdns: An existing AsyncZeroconf instance. If None, a new instance will be created.
            timeout: The timeout for mDNS operations in seconds.
        """
        self._async_zercnf = async_mdns if async_mdns is not None else AsyncZeroconf()
        self._mdns_timeout = timeout

        self._service_type = (
            "_" + ApplicationConfig.service_name.lower() + "._tcp.local."
        )
        self._uid: Optional[str] = None

        self._logger = get_logger(self.__class__.__name__)

    @staticmethod
    def generate_uid(host: str) -> str:
        """
        It generates a unique identifier for the service instance.

        Args:
            host: An IP addr where the service is running.
        Returns:
            A unique identifier string.
        """
        try:
            return UIDGenerator.generate_uid(host, ServiceDiscovery.UID_LEN)
        except Exception as e:
            raise RuntimeError(f"Failed to generate UID ({e})")

    def get_uid(self) -> Optional[str]:
        """
        It returns the unique identifier for the service instance.

        Returns:
            A unique identifier string.
        """
        return self._uid

    @staticmethod
    async def resolve_hostname(hostname: str):
        """
        Resolve a machine hostname to an IP address (no mDNS).
        """
        try:
            ip_address = get_local_ip()
            await asyncio.sleep(0)
            return ip_address
        except Exception as e:
            raise RuntimeError(f"Failed to resolve hostname {hostname} ({e})")

    @staticmethod
    def _is_ip(ip: str) -> bool:
        """
        Use socket utilities to check if a string is a valid IPv4 or IPv6 address.
        """
        for family in (socket.AF_INET, socket.AF_INET6):
            try:
                socket.inet_pton(family, ip)
                return True
            except OSError:
                continue
        return False

    async def _register_service(self, host: str, port: int) -> None:
        """
        Registers a network service using mDNS. This allows the service
        to be discoverable on the local network by other devices. The method validates
        the provided host and ensures all necessary internal configurations are properly
        set before registration.

        Args:
            host (str): The hostname or IP address the service is bound to. If a hostname
                is provided, it will be resolved to an IP address.
            port (int): The network port on which the service is running.

        Raises:
            ValueError: If the host is an empty string, or if the service type or name
                is invalid.
            RuntimeError: If the Zeroconf instance is not initialized, or if the service
                registration fails due to an internal issue.
        """
        if host == "":
            raise ValueError("Host cannot be an empty string")

        if self._async_zercnf is None:
            raise RuntimeError("Zeroconf instance is not initialized")

        if not self._is_ip(host):
            hostname = host
            host = await self.resolve_hostname(host)
        else:
            hostname = socket.gethostname()

        if self._uid is None:
            self._uid = ServiceDiscovery.generate_uid(host)

        service_name = ".".join([self._uid, self._service_type])
        # Build service info
        try:
            properties = {"hostname": hostname}
            s_info = ServiceInfo(
                type_=self._service_type,
                name=service_name,
                parsed_addresses=[host],
                port=port,
                properties=properties,
            )

            await self._async_zercnf.async_register_service(s_info)
            self._logger.info(
                "mDNS service registered.", uid=self._uid, port=port, **properties
            )
        except BadTypeInNameException:
            raise ValueError("Invalid service type or name")
        except Exception as e:
            import traceback
            print(traceback.format_exc())
            raise RuntimeError(f"Failed to register mDNS service ({e})")

    async def _unregister_service(self):
        if self._async_zercnf is None:
            raise RuntimeError("Zeroconf instance is not initialized")

        await self._async_zercnf.async_unregister_all_services()
        self._logger.info("mDNS service unregistered.")

    async def _discover_services(self) -> list[Service]:
        """
        It discovers services on the network using mDNS.
        Returns:
            A list of discovered services.

        Raises:
            RuntimeError: If discovery fails.
        """
        if self._async_zercnf is None:
            raise RuntimeError("Zeroconf instance is not initialized")

        listener = _ServiceListener()
        try:
            zconf = self._async_zercnf.zeroconf
            if zconf is None:
                raise RuntimeError("Zeroconf instance is not initialized")

            browser = AsyncServiceBrowser(
                zeroconf=zconf, type_=self._service_type, listener=listener
            )
        except Exception as e:
            raise RuntimeError(f"Failed to start mDNS service discovery ({e})")
        finally:
            listener.clear()

        try:
            await asyncio.sleep(self._mdns_timeout)
            self._logger.info(
                f"Discovered {len(listener.get_services())} mDNS services."
            )

            return listener.get_services()

        except Exception as e:
            raise RuntimeError(f"Failed to discover mDNS services ({e})")
        finally:
            await browser.async_cancel()
            zconf.close()
            listener.clear()

    # async def _resolve_mdns(self):
    #     """
    #     It resolves an hostname to an IP address using mDNS.
    #     """
    #     pass

    async def register_service(
        self, host: str, port: int, uid: Optional[str] = None
    ) -> None:
        """
        It registers a service on the network using mDNS.

        Args:
            host: An IP addr where the service is running.
            port: The port where the service is running.
            uid: A unique identifier for the service instance. If None, a new UID will be generated.

        Raises:
            ValueError: If the host is an empty string, or if the service type or name
                is invalid.
            RuntimeError: If the Zeroconf instance is not initialized, or if the service
                registration fails due to an internal issue.

        """
        if uid is not None:
            self._uid = uid
        # TODO: We need to check if there is another service on same host/port
        await self._register_service(host, port)

    async def unregister_service(self):
        """
        It unregisters the service from the network using mDNS.

        Raises:
            RuntimeError: If unregistration fails.
        """
        await self._unregister_service()

    async def discover_services(self) -> list[Service]:
        """
        It discovers services on the network using mDNS.

        Returns:
            A list of discovered services.

        Raises:
            RuntimeError: If discovery fails.
        """
        services = await self._discover_services()
        return services
