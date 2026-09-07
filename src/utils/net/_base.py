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


from dataclasses import dataclass
from typing import Any, Optional

import ipaddress
import socket

import ifaddr


class MissingIpError(Exception):
    """Custom exception raised when the local IP address cannot be determined."""

    pass


@dataclass(frozen=True, slots=True)
class LocalInterface:
    """One (adapter, IPv4 address) pair present on this machine.

    One record per address rather than per adapter: an adapter can hold
    several addresses and what the admin actually picks is a link.
    """

    name: str
    display_name: str
    ip: str
    prefix: int
    cidr: str
    is_default_route: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "ip": self.ip,
            "prefix": self.prefix,
            "cidr": self.cidr,
            "is_default_route": self.is_default_route,
        }


def _is_usable_ip(ip: str | int) -> bool:
    """Reject addresses that cannot be advertised to peers on the LAN."""
    if not ip:
        return False
    if isinstance(ip, int):
        ip = socket.inet_ntoa(ip.to_bytes(4, "big"))
    if ip.startswith("127.") or ip.startswith("169.254."):
        return False
    if ip in ("0.0.0.0", "::", "::1"):
        return False
    return True


class CommonNetInfo:
    """Common network information class for shared attributes or methods."""

    # Unicast route probe target. Only used to ask the routing table which
    # local address it would source from; no packet is ever sent.
    HOST: str = "8.8.8.8"
    PORT: int = 53
    TIMEOUT: int = 3

    # Link-local multicast (mDNS) probe target. Reachable without a default
    # route, so it still resolves an address on a LAN with no WAN uplink.
    MULTICAST_HOST: str = "224.0.0.251"
    MULTICAST_PORT: int = 5353

    @staticmethod
    def _probe_route(host: str, port: int) -> str:
        """Which local address it would use to reach ``host``."""
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(CommonNetInfo.TIMEOUT)
            s.connect((host, port))
            return s.getsockname()[0]

    @staticmethod
    def _resolve_own_hostname() -> str:
        """Last-resort lookup: resolve this machine's hostname to a LAN IPv4."""
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if _is_usable_ip(ip):
                return (
                    ip
                    if isinstance(ip, str)
                    else socket.inet_ntoa(ip.to_bytes(4, "big"))
                )
        raise MissingIpError("hostname resolves to no usable address")

    @staticmethod
    def get_local_ip() -> str:
        """
        Retrieves the local IP address of the current machine without sending
        any data, trying progressively weaker strategies:

        1. route probe towards a public host (needs a default route);
        2. route probe towards the mDNS multicast group (works on a LAN with
           no internet access at all);
        3. resolution of the machine's own hostname.

        :raises MissingIpError: If none of the strategies yields a usable
            address; the last underlying error is embedded in the message.
        :return: The local IP address of the machine as a string.
        :rtype: str
        """
        last_error: Exception = MissingIpError("no strategy available")

        for strategy in (
            lambda: CommonNetInfo._probe_route(CommonNetInfo.HOST, CommonNetInfo.PORT),
            lambda: CommonNetInfo._probe_route(
                CommonNetInfo.MULTICAST_HOST, CommonNetInfo.MULTICAST_PORT
            ),
            CommonNetInfo._resolve_own_hostname,
        ):
            try:
                ip = strategy()
            except Exception as e:  # noqa: BLE001 - try the next strategy
                last_error = e
                continue
            if _is_usable_ip(ip):
                return ip

        raise MissingIpError(
            f"Could not determine local IP address ({last_error})"
        ) from last_error

    @staticmethod
    def list_local_interfaces(
        include_unusable: bool = False,
        default_route_ip: Optional[str] = None,
    ) -> list["LocalInterface"]:
        """Every usable IPv4 address on this machine, with its adapter.

        ``get_local_ip`` answers "which address reaches the internet"; this
        answers "which addresses exist at all", which is the question a
        multi-homed host actually needs answered before advertising itself.

        :param include_unusable: keep loopback/link-local, which
            ``_is_usable_ip`` normally rejects. The interface picker wants
            them (a direct cable with no DHCP lands on 169.254/16);
            auto-selection does not.
        :param default_route_ip: the address ``get_local_ip`` would return.
            Injected by callers so the module-level cache is reused instead
            of firing a second route probe, and so tests stay pure. Resolved
            internally when ``None``.
        :return: records ordered default-route first, then by
            ``(display_name, ip)``. Never raises: an empty list means "no
            usable address right now", which every caller handles.
        """
        if default_route_ip is None:
            try:
                default_route_ip = CommonNetInfo.get_local_ip()
            except Exception:  # noqa: BLE001 - flagging is best-effort
                default_route_ip = None

        interfaces: list[LocalInterface] = []
        try:
            for adapter in ifaddr.get_adapters():
                for entry in adapter.ips:
                    # ifaddr hands back a (addr, flowinfo, scope_id) tuple for
                    # IPv6 and a plain string for IPv4; is_IPv4 is the
                    # documented discriminator.
                    if not entry.is_IPv4:
                        continue
                    ip = entry.ip
                    if not include_unusable and not _is_usable_ip(ip):
                        continue
                    prefix = int(entry.network_prefix)
                    try:
                        cidr = str(ipaddress.ip_network(f"{ip}/{prefix}", strict=False))
                    except ValueError:
                        cidr = f"{ip}/{prefix}"
                    interfaces.append(
                        LocalInterface(
                            name=adapter.name,
                            display_name=adapter.nice_name or adapter.name,
                            ip=ip,
                            prefix=prefix,
                            cidr=cidr,
                            is_default_route=(ip == default_route_ip),
                        )
                    )
        except Exception:  # noqa: BLE001 - enumeration must never break a start
            return []

        interfaces.sort(key=lambda i: (not i.is_default_route, i.display_name, i.ip))
        return interfaces
