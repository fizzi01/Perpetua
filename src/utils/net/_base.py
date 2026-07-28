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


import socket


class MissingIpError(Exception):
    """Custom exception raised when the local IP address cannot be determined."""

    pass


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
