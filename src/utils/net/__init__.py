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

from utils import backend_module
from utils.logging import get_logger
from typing import TYPE_CHECKING, Optional

import asyncio
import ipaddress
import socket
import time

from ._base import LocalInterface, _is_usable_ip

if TYPE_CHECKING:
    from ._base import CommonNetInfo, MissingIpError

    _platform_get_local_ip = CommonNetInfo.get_local_ip
    _platform_list_interfaces = CommonNetInfo.list_local_interfaces
else:
    _backend_module = backend_module(__name__)
    CommonNetInfo = _backend_module.CommonNetInfo
    MissingIpError = _backend_module.MissingIpError
    _platform_get_local_ip = CommonNetInfo.get_local_ip
    _platform_list_interfaces = CommonNetInfo.list_local_interfaces
    del _backend_module


# Public alias: callers that filter addresses must reuse this rule rather than
# re-deriving "is this advertisable", or the two copies drift.
is_usable_ip = _is_usable_ip

_logger = get_logger("net")


# Cached result + monotonic timestamp. The IP is fetched by opening a UDP-style
# socket and reading getsockname, which is cheap but not free; callers invoke
# this on every service-discovery tick and every client (dis)connect.
_LOCAL_IP_TTL: float = 30.0
_local_ip_cache: Optional[str] = None
_local_ip_cache_ts: float = 0.0


def get_local_ip(force_refresh: bool = False) -> str:
    global _local_ip_cache, _local_ip_cache_ts
    now = time.monotonic()
    if (
        not force_refresh
        and _local_ip_cache is not None
        and (now - _local_ip_cache_ts) < _LOCAL_IP_TTL
    ):
        return _local_ip_cache
    ip = _platform_get_local_ip()
    _local_ip_cache = ip
    _local_ip_cache_ts = now
    return ip


def invalidate_local_ip_cache() -> None:
    """Force the next get_local_ip() call to re-query the platform."""
    global _local_ip_cache, _local_ip_cache_ts
    _local_ip_cache = None
    _local_ip_cache_ts = 0.0


# Deliberately uncached: the GUI picker and the advertise resolution must never
# disagree about what exists right now. Enumeration is cheap enough that a
# second TTL would buy nothing but a window of inconsistency.
def list_local_interfaces(include_unusable: bool = False) -> list[LocalInterface]:
    """Usable IPv4 addresses on this machine. Never raises; [] when none."""
    try:
        default_route_ip = get_local_ip()
    except Exception:  # noqa: BLE001 - flagging the default route is optional
        default_route_ip = None
    return _platform_list_interfaces(
        include_unusable=include_unusable, default_route_ip=default_route_ip
    )


async def list_local_interfaces_async(
    include_unusable: bool = False,
) -> list[LocalInterface]:
    """``list_local_interfaces`` off the event loop.

    Mandatory from coroutines: on Windows enumeration goes through
    GetAdaptersAddresses, which can take tens of milliseconds - enough to
    stutter the cursor worker if it runs inline.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None, lambda: list_local_interfaces(include_unusable)
    )


def is_bind_all(host: Optional[str]) -> bool:
    """Does ``ServerConfig.host`` mean "every interface"?

    ``"0.0.0.0"`` is the default and the value in every config written before
    the address became selectable from the GUI, so treating it as the wildcard
    is what lets those carry over untouched.
    """
    return not host or host.strip() in ("0.0.0.0", "")


def normalize_bind_host(host: Optional[str]) -> str:
    """``host`` as an address a socket can actually bind.

    Only an IPv4 literal or the wildcard survives. A name - an adapter, a
    hostname, a hand-edited typo - is discarded in favour of the wildcard:
    the server still comes up on every interface instead of failing to bind,
    and the name would otherwise reach the certificate SAN as a DNS entry
    that no client resolves.
    """
    if is_bind_all(host):
        return "0.0.0.0"

    candidate = (host or "").strip()
    try:
        ipaddress.IPv4Address(candidate)
    except ValueError:
        _logger.warning(
            "Ignoring a bind address that is not an IPv4 literal; "
            "listening on every interface",
            host=candidate,
        )
        return "0.0.0.0"
    return candidate


def resolve_advertise_addresses(
    host: Optional[str],
    interfaces: Optional[list[LocalInterface]] = None,
) -> list[str]:
    """Addresses to advertise over mDNS and bake into the certificate SAN.

    Derived from the bind address rather than configured separately: the
    listener is only reachable where it is bound, so advertising anything
    else invites clients to an address that will refuse them.

    :param interfaces: injected snapshot; keeps the function pure and lets a
        caller reuse one enumeration for both the SAN and the mDNS record so
        the two can never diverge across a link flap. Only consulted for the
        wildcard - a concrete address is already the answer.
    :return: de-duplicated addresses. Empty only when the machine binds every
        interface and has no usable address at all.
    """
    if not is_bind_all(host):
        return [(host or "").strip()]

    if interfaces is None:
        interfaces = list_local_interfaces(include_unusable=True)
    return list(dict.fromkeys(i.ip for i in interfaces if is_usable_ip(i.ip)))


def set_socket_nodelay(writer: "asyncio.StreamWriter") -> None:
    # input deltas are tiny and latency-critical.
    sock = writer.get_extra_info("socket")
    if sock is None:
        return
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except OSError:
        pass
