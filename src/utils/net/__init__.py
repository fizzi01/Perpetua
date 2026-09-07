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


def resolve_advertise_addresses(
    preference: Optional[str],
    interfaces: Optional[list[LocalInterface]] = None,
) -> list[str]:
    """Addresses to advertise over mDNS and bake into the certificate SAN.

    ``preference`` is ``ServerConfig.host``, which is an *interface
    preference*, not a bind address - the listener always binds BIND_ALL.

    Matching accepts, in order, an adapter name, an IP literal or a friendly
    name: hand-edited configs and configs copied between machines both
    degrade sanely.

    :param interfaces: injected snapshot; keeps the function pure and lets a
        caller reuse one enumeration for both the SAN and the mDNS record so
        the two can never diverge across a link flap.
    :return: chosen address first, remaining usable ones after, de-duplicated.
        Empty only when the machine genuinely has no usable address.
    """
    if interfaces is None:
        interfaces = list_local_interfaces()

    usable = [i.ip for i in interfaces]

    # "0.0.0.0" is the historical default and the value in every untouched
    # 1.6.0 config; treating it as "auto" is what lets legacy configs carry
    # over with no migration step.
    if not preference or preference == "0.0.0.0":
        return list(dict.fromkeys(usable))

    wanted = preference.strip()
    lowered = wanted.lower()
    chosen = [
        i.ip
        for i in interfaces
        if i.name == wanted
        or i.ip.lower() == lowered
        or i.display_name.lower() == lowered
    ]

    if not chosen:
        # Cable unplugged, adapter renamed. Fall back to auto rather than
        # returning [] - being invisible on the network is worse than
        # advertising too much - and never rewrite the stored preference:
        # the NIC may come back, and silently discarding the admin's choice
        # is the very bug class this whole change exists to fix.
        _logger.warning(
            "Advertise interface not found, falling back to all interfaces",
            preference=wanted,
            available=usable,
        )
        return list(dict.fromkeys(usable))

    return list(dict.fromkeys([*chosen, *usable]))


def resolve_advertise_interfaces(
    preference: Optional[str],
    interfaces: Optional[list[LocalInterface]] = None,
) -> list[str]:
    """Addresses to run a *separate* mDNS responder on, one each.

    Distinct from ``resolve_advertise_addresses``, which answers "what goes in
    the TXT and the certificate SAN". This answers "where do we speak, and as
    whom" - one responder per address means a client on a given link receives
    a record containing an address reachable *on that link*, with no TXT and
    no probing needed. That is what makes a direct cable work for a client
    that has not been upgraded.

    Auto advertises on every usable interface; an explicit preference speaks
    only on the interface it names, which is what "Advertise on: eth1" should
    plainly mean.
    """
    if interfaces is None:
        interfaces = list_local_interfaces()

    if not preference or preference == "0.0.0.0":
        return list(dict.fromkeys(i.ip for i in interfaces))

    wanted = preference.strip()
    lowered = wanted.lower()
    chosen = [
        i.ip
        for i in interfaces
        if i.name == wanted
        or i.ip.lower() == lowered
        or i.display_name.lower() == lowered
    ]
    # Stale preference: fall back to speaking everywhere rather than going
    # silent. resolve_advertise_addresses logs the warning for this case.
    return list(dict.fromkeys(chosen)) or list(dict.fromkeys(i.ip for i in interfaces))


def set_socket_nodelay(writer: "asyncio.StreamWriter") -> None:
    # input deltas are tiny and latency-critical.
    sock = writer.get_extra_info("socket")
    if sock is None:
        return
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except OSError:
        pass
