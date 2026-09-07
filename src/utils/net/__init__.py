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


def is_auto_preference(preference: Optional[str]) -> bool:
    """Does ``ServerConfig.host`` mean "every interface"?

    ``"0.0.0.0"`` is the historical default and the value in every config
    written before ``host`` became a preference; treating it as auto is what
    lets those carry over with no migration step.
    """
    return not preference or preference.strip() == "0.0.0.0"


def match_interface(
    preference: Optional[str],
    interfaces: Optional[list[LocalInterface]] = None,
) -> list[str]:
    """Addresses the admin's explicit choice names. **No fallback.**

    Matching accepts an adapter name, an IP literal or a friendly name:
    hand-edited configs and configs copied between machines both degrade
    sanely.

    Every address in ``interfaces`` is a candidate, loopback and link-local
    included. ``_is_usable_ip`` decides what *automatic* selection may pick;
    it is not a veto on an explicit choice, and the picker offers these, so
    refusing to match them here is how "advertise on 127.0.0.1" silently
    became "advertise on everything".

    :return: the matching addresses, or ``[]`` when the choice names nothing
        present. Callers decide what an unmatched choice should mean - which
        is deliberately *not* the same answer for advertising and for access
        control.
    """
    if is_auto_preference(preference):
        return []
    if interfaces is None:
        interfaces = list_local_interfaces(include_unusable=True)

    wanted = (preference or "").strip()
    lowered = wanted.lower()
    return list(
        dict.fromkeys(
            i.ip
            for i in interfaces
            if i.name == wanted
            or i.ip.lower() == lowered
            or i.display_name.lower() == lowered
        )
    )


def follow_interface_address(
    preference: Optional[str],
    previous: Optional[list[LocalInterface]],
    current: Optional[list[LocalInterface]] = None,
) -> Optional[str]:
    """The address ``preference`` moved to, or ``None`` to leave it alone.

    ``ServerConfig.host`` stores an address because that is what reads well in
    a config file, but a DHCP renewal changes it and the selection then
    matches nothing: the server quietly falls back to advertising everything,
    or - with ``host_exclusive`` - stops accepting connections.

    Following it needs evidence that it is the same link, and only two count:

    1. the adapter the address sat on in ``previous`` still exists, so its
       current address is the same link;
    2. failing that (no snapshot, e.g. right after a restart), exactly one
       interface's subnet contains the old address.

    Anything less is a guess, and a wrong guess moves the server onto an
    interface the admin never picked. A vanished adapter is deliberately *not*
    followed: the choice is kept so the interface can come back.
    """
    if is_auto_preference(preference):
        return None
    if current is None:
        current = list_local_interfaces(include_unusable=True)

    wanted = (preference or "").strip()
    try:
        old_addr = ipaddress.ip_address(wanted)
    except ValueError:
        # Names already survive a renewal; there is nothing to follow.
        return None

    if any(i.ip == wanted for i in current):
        return None

    # (1) same adapter, new address.
    if previous:
        adapters = {i.name for i in previous if i.ip == wanted}
        for iface in current:
            if iface.name in adapters and iface.ip != wanted:
                return iface.ip

    # (2) unambiguous subnet match.
    same_subnet = []
    for iface in current:
        try:
            net = ipaddress.ip_network(f"{iface.ip}/{iface.prefix}", strict=False)
        except ValueError:
            continue
        if old_addr in net:
            same_subnet.append(iface.ip)
    if len(same_subnet) == 1:
        return same_subnet[0]

    return None


def resolve_advertise_addresses(
    preference: Optional[str],
    interfaces: Optional[list[LocalInterface]] = None,
    exclusive: bool = False,
) -> list[str]:
    """Addresses to advertise over mDNS and bake into the certificate SAN.

    ``preference`` is ``ServerConfig.host``, which is an *interface
    preference*, not a bind address - the listener always binds BIND_ALL.

    :param interfaces: injected snapshot; keeps the function pure and lets a
        caller reuse one enumeration for both the SAN and the mDNS record so
        the two can never diverge across a link flap.
    :param exclusive: when the admin restricted access to the chosen
        interface, advertise *only* it. Publishing addresses that the accept
        filter will then refuse is worse than publishing fewer.
    :return: chosen address first, remaining usable ones after, de-duplicated.
        Empty only when the machine genuinely has no usable address.
    """
    if interfaces is None:
        interfaces = list_local_interfaces(include_unusable=True)

    auto = [i.ip for i in interfaces if is_usable_ip(i.ip)]

    if is_auto_preference(preference):
        return list(dict.fromkeys(auto))

    chosen = match_interface(preference, interfaces)
    if not chosen:
        if exclusive:
            # The accept filter refuses everything while the chosen interface
            # is absent, so advertising anything here would invite clients to
            # an address they are then turned away from. Announce nothing.
            _logger.warning(
                "Advertise interface not found and access is restricted to it; "
                "advertising nothing",
                preference=preference,
                available=auto,
            )
            return []
        # Cable unplugged, adapter renamed. Fall back to auto rather than
        # returning [] - being invisible on the network is worse than
        # advertising too much - and never rewrite the stored preference:
        # the NIC may come back, and silently discarding the admin's choice
        # is the very bug class this whole change exists to fix.
        _logger.warning(
            "Advertise interface not found, falling back to all interfaces",
            preference=preference,
            available=auto,
        )
        return list(dict.fromkeys(auto))

    if exclusive:
        return chosen
    return list(dict.fromkeys([*chosen, *auto]))


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

    Auto speaks on every usable interface; an explicit preference speaks only
    on the interface it names, which is what "Advertise on: eth1" should
    plainly mean.
    """
    if interfaces is None:
        interfaces = list_local_interfaces(include_unusable=True)

    auto = [i.ip for i in interfaces if is_usable_ip(i.ip)]
    if is_auto_preference(preference):
        return list(dict.fromkeys(auto))

    # Stale preference: fall back to speaking everywhere rather than going
    # silent. resolve_advertise_addresses logs the warning for this case.
    return match_interface(preference, interfaces) or list(dict.fromkeys(auto))


def set_socket_nodelay(writer: "asyncio.StreamWriter") -> None:
    # input deltas are tiny and latency-critical.
    sock = writer.get_extra_info("socket")
    if sock is None:
        return
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except OSError:
        pass
