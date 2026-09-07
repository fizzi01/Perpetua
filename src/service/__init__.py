"""
Service package provides server and client public APIs.
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

from utils import UIDGenerator

import asyncio
from typing import Dict, List, Optional, Tuple
import socket

from zeroconf import (
    ServiceInfo,
    ServiceListener,
    Zeroconf,
    BadTypeInNameException,
    NonUniqueNameException,
)
from zeroconf.asyncio import AsyncZeroconf, AsyncServiceBrowser, AsyncServiceInfo

from config import ApplicationConfig
from utils.logging import get_logger
from utils.net import is_usable_ip, CommonNetInfo


def _txt_int(props: dict, key: bytes) -> Optional[int]:
    """Extract an integer value from a zeroconf TXT record, gracefully."""
    raw = props.get(key)
    if raw is None or raw == b"":
        return None
    try:
        return int(raw.decode())
    except (UnicodeDecodeError, ValueError):
        return None


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
        pairing_port: Optional[int] = None,
        addresses: Optional[List[str]] = None,
    ):
        """
        An mDNS service instance.

        Args:
            name: Service name.
            address: Preferred service IP address.
            port: Service port.
            hostname: Service hostname.
            uid: Service unique identifier.
            pairing_port: Optional plaintext pairing/cert-sharing port
                advertised in the TXT record. ``None`` for legacy servers.
            addresses: Every address the server advertised, preferred first.
                A multi-homed server publishes them all so a client that
                cannot reach the first can fall back instead of giving up.
                Defaults to ``[address]`` for legacy servers.
        """
        self.uid = uid
        self.name = name
        self.addresses: List[str] = list(
            dict.fromkeys(addresses or ([address] if address else []))
        )
        self.hostname: Optional[str] = hostname
        self.port = port
        self.pairing_port: Optional[int] = pairing_port

    @property
    def address(self) -> str:
        """The preferred address; ``""`` when the service advertised none."""
        return self.addresses[0] if self.addresses else ""

    @address.setter
    def address(self, value: str) -> None:
        """Promote ``value`` to preferred, keeping the rest as fallbacks."""
        if value:
            self.addresses = [value] + [a for a in self.addresses if a != value]

    def as_dict(self) -> dict:
        """
        It returns the service as a dictionary.
        """
        return {
            "uid": self.uid,
            # "name": self.name,
            "address": self.address,
            # Additive: old GUIs keep reading "address" and ignore this.
            "addresses": list(self.addresses),
            "hostname": self.hostname,
            "port": self.port,
            "pairing_port": self.pairing_port,
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

    @staticmethod
    def _candidate_addresses(info: "AsyncServiceInfo") -> List[str]:
        """Every address this server can be reached at, preferred first.

        The A records come first - zeroconf hands them back in receive order,
        so entry 0 is whatever arrived last, not what the server prefers - and
        the ``addresses`` TXT list fills in the links whose records this
        client never saw. A server reachable on several links publishes them
        all so a client on a different link can fall back instead of failing.
        """
        addresses = list(info.parsed_addresses())
        raw = info.properties.get(b"addresses")
        if raw:
            try:
                advertised = raw.decode().split(",")
            except UnicodeDecodeError:
                advertised = []
            for entry in advertised:
                entry = entry.strip()
                if entry and entry not in addresses:
                    addresses.append(entry)
        return list(dict.fromkeys(a for a in addresses if a))

    @staticmethod
    def _txt_hostname(info: "AsyncServiceInfo") -> Optional[str]:
        """Decode the hostname TXT, mapping an empty value to None.

        An empty string is not a hostname: it poisons the identity checks that
        compare against the saved server.
        """
        raw = info.properties.get(b"hostname")
        if not raw:
            return None
        try:
            decoded = raw.decode().strip()
        except UnicodeDecodeError:
            return None
        return decoded or None

    async def _service_info_task(self, zc: Zeroconf, type_: str, name: str):
        # Get service info
        info = AsyncServiceInfo(type_=type_, name=name)
        await info.async_request(zc=zc, timeout=3000)
        if info is not None and len(info.parsed_addresses()) > 0:
            addresses = self._candidate_addresses(info)
            uid = name.split(".")[0]
            hostname = self._txt_hostname(info)
            pairing_port = _txt_int(info.properties, b"pairing_port")

            service = Service(
                name,
                addresses[0] if addresses else "",
                info.port,
                uid=uid,
                hostname=hostname,
                pairing_port=pairing_port,
                addresses=addresses,
            )

            # Re-announcements fire add_service again; without this the same
            # server accumulates duplicate entries, which then skews the
            # "exactly one server found" auto-selection.
            for existing in self._services:
                if existing.uid == uid:
                    self._services.remove(existing)
                    break
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
                    # Full replace, so an address the server withdrew goes away
                    # instead of lingering as a candidate forever.
                    service.addresses = self._candidate_addresses(info)
                    service.port = info.port
                    hostname = self._txt_hostname(info)
                    if hostname is not None:
                        service.hostname = hostname
                    pp = _txt_int(info.properties, b"pairing_port")
                    if pp is not None:
                        service.pairing_port = pp
                    break

    def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        """Update existing service"""
        task = asyncio.create_task(self._service_info_update_task(zc, type_, name))
        self._pending_task.add(task)
        task.add_done_callback(self._pending_task.discard)

    def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        """Remove a service that has left the network."""
        uid = name.split(".")[0]
        self._services = [s for s in self._services if s.uid != uid]

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
        # True when we built the instance ourselves and may therefore create
        # extra per-interface ones alongside it. An injected instance (tests,
        # callers that own the lifecycle) is used exactly as given.
        self._owns_zeroconf = async_mdns is None
        self._mdns_timeout = timeout

        self._service_type = (
            "_" + ApplicationConfig.service_name.lower() + "._tcp.local."
        )
        self._uid: Optional[str] = None

        # One responder per advertised interface, each announcing only that
        # interface's own address. A client on a given link then receives an
        # address reachable on that link without reading TXT or probing -
        # which is what makes an unupgraded client work on a direct cable.
        self._iface_responders: List[Tuple[AsyncZeroconf, ServiceInfo]] = []

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

        Previously this ignored its argument and returned ``get_local_ip()``,
        i.e. *our own* default-route address regardless of which host was
        asked about - the same multi-homing mistake this module exists to fix.
        """
        try:
            loop = asyncio.get_running_loop()
            infos = await loop.run_in_executor(
                None,
                lambda: socket.getaddrinfo(hostname, None, socket.AF_INET),
            )
        except Exception as e:
            raise RuntimeError(f"Failed to resolve hostname {hostname} ({e})")

        for info in infos:
            ip = info[4][0]
            if is_usable_ip(ip):
                return ip
        raise RuntimeError(f"Hostname {hostname} resolves to no usable address")

    @staticmethod
    def _is_loopback(ip: str) -> bool:
        """
        Check if the given IP address is a loopback address.
        """
        if ip.startswith("127.") or ip == "::1" or ip == "0.0.0.0" or ip == "::":
            return True

        return False

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

    async def _register_service(
        self,
        host: str,
        port: int,
        extra_props: Optional[Dict[str, str]] = None,
        interface_addresses: Optional[List[str]] = None,
    ) -> None:
        """
        Registers a network service using mDNS. This allows the service
        to be discoverable on the local network by other devices. The method validates
        the provided host and ensures all necessary internal configurations are properly
        set before registration.

        Args:
            host (str): The hostname or IP address the service is bound to. If a hostname
                is provided, it will be resolved to an IP address.
            port (int): The network port on which the service is running.
            extra_props: Additional TXT entries, coerced to ``str``.
            interface_addresses: Advertise from a dedicated responder on each
                of these addresses, announcing that address only. Falls back
                to one responder on every interface announcing ``host`` -
                the pre-existing behaviour - when omitted or unusable.

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
            if self._is_loopback(host):
                # "0.0.0.0" is a bind wildcard, never an advertisable address.
                # Prefer the caller's resolved list; the route probe is only
                # the last resort now.
                if interface_addresses:
                    host = interface_addresses[0]
                else:
                    host = CommonNetInfo.get_local_ip()
            hostname = socket.gethostname()

        # Mint the UID *after* the address is settled: on a first run it is
        # derived from it. Existing installs pass a persisted uid, so this
        # never re-mints for them.
        if self._uid is None:
            self._uid = ServiceDiscovery.generate_uid(host)

        service_name = ".".join([self._uid, self._service_type])
        # Build service info
        try:
            properties: Dict[str, str] = {"hostname": hostname}
            if extra_props:
                # Stringify everything: zeroconf's TXT records are bytes
                # under the hood and stringly typed by convention.
                for k, v in extra_props.items():
                    if v is None:
                        continue
                    properties[str(k)] = str(v)

            targets = [a for a in (interface_addresses or []) if self._is_ip(a)]
            if self._owns_zeroconf and targets:
                await self._register_per_interface(
                    targets, service_name, port, properties
                )
                return

            s_info = ServiceInfo(
                type_=self._service_type,
                name=service_name,
                parsed_addresses=[host],
                port=port,
                properties=properties,
            )

            await self._async_zercnf.async_register_service(s_info)
            self._logger.info(
                "mDNS service registered.",
                uid=self._uid,
                port=port,
                host=host,
                **properties,
            )
        except BadTypeInNameException:
            raise ValueError("Invalid service type or name")
        except NonUniqueNameException:
            raise RuntimeError("Service name is already in use on the network")
        except Exception as e:
            self._logger.exception("Unhandled service error", error=str(e))
            raise RuntimeError(f"Failed to register mDNS service ({e})")

    async def _register_per_interface(
        self,
        addresses: List[str],
        service_name: str,
        port: int,
        properties: Dict[str, str],
    ) -> None:
        """One responder per address, each announcing only its own address.

        A client sees a record whose A entry is reachable on the very link the
        packet arrived on. Where a client can see several of our links the
        records merge in its cache, which is what the TXT address list and the
        client-side probe are there to resolve.
        """
        await self._unregister_iface_responders()

        registered: List[str] = []
        for ip in addresses:
            try:
                azc = AsyncZeroconf(interfaces=[ip])
                s_info = ServiceInfo(
                    type_=self._service_type,
                    name=service_name,
                    parsed_addresses=[ip],
                    port=port,
                    properties=properties,
                )
                await azc.async_register_service(s_info)
            except Exception as e:  # noqa: BLE001 - one bad NIC must not stop the rest
                self._logger.warning(
                    "Could not advertise on interface", address=ip, error=str(e)
                )
                continue
            self._iface_responders.append((azc, s_info))
            registered.append(ip)

        if not registered:
            raise RuntimeError("Failed to register mDNS service on any interface")

        self._logger.info(
            "mDNS service registered per interface.",
            uid=self._uid,
            port=port,
            addresses=registered,
            **properties,
        )

    async def _unregister_iface_responders(self) -> None:
        """Tear down every per-interface responder, best effort."""
        for azc, s_info in self._iface_responders:
            try:
                await azc.async_unregister_service(s_info)
            except Exception as e:  # noqa: BLE001 - teardown is best effort
                self._logger.debug(f"Per-interface unregister failed ({e})")
            try:
                await azc.async_close()
            except Exception as e:  # noqa: BLE001
                self._logger.debug(f"Per-interface close failed ({e})")
        self._iface_responders.clear()

    async def _unregister_service(self):
        if self._async_zercnf is None:
            raise RuntimeError("Zeroconf instance is not initialized")

        await self._unregister_iface_responders()
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

        # Allocate the listener and browser handle outside the try so the
        # ``finally`` cleanup is reachable on any exception path.
        listener = _ServiceListener()
        browser: Optional[AsyncServiceBrowser] = None
        zconf = self._async_zercnf.zeroconf
        if zconf is None:
            raise RuntimeError("Zeroconf instance is not initialized")

        try:
            browser = AsyncServiceBrowser(
                zeroconf=zconf, type_=self._service_type, listener=listener
            )
            await asyncio.sleep(self._mdns_timeout)
            self._logger.info(
                f"Discovered {len(listener.get_services())} mDNS services."
            )
            return listener.get_services()
        except Exception as e:
            raise RuntimeError(f"Failed to discover mDNS services ({e})")
        finally:
            if browser is not None:
                try:
                    await browser.async_cancel()
                except Exception:
                    pass
            try:
                zconf.close()
            except Exception:
                pass
            listener.clear()

    # async def _resolve_mdns(self):
    #     """
    #     It resolves an hostname to an IP address using mDNS.
    #     """
    #     pass

    async def register_service(
        self,
        host: str,
        port: int,
        uid: Optional[str] = None,
        extra_props: Optional[Dict[str, str]] = None,
        interface_addresses: Optional[List[str]] = None,
    ) -> None:
        """
        It registers a service on the network using mDNS.

        Args:
            host: An IP addr where the service is running.
            port: The port where the service is running.
            uid: A unique identifier for the service instance. If None, a new UID will be generated.
            extra_props: Additional TXT record entries to advertise (e.g.
                ``{"pairing_port": "55653"}``). Values are coerced to ``str``.

        Raises:
            ValueError: If the host is an empty string, or if the service type or name
                is invalid.
            RuntimeError: If the Zeroconf instance is not initialized, or if the service
                registration fails due to an internal issue.

        """
        if uid is not None:
            self._uid = uid
        # TODO: We need to check if there is another service on same host/port
        await self._register_service(
            host,
            port,
            extra_props=extra_props,
            interface_addresses=interface_addresses,
        )

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
