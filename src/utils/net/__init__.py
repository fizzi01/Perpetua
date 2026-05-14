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
from typing import TYPE_CHECKING, Optional

import asyncio
import socket
import time

if TYPE_CHECKING:
    from ._base import CommonNetInfo

    _platform_get_local_ip = CommonNetInfo.get_local_ip
else:
    _backend_module = backend_module(__name__)
    CommonNetInfo = _backend_module.CommonNetInfo
    MissingIpError = _backend_module.MissingIpError
    _platform_get_local_ip = CommonNetInfo.get_local_ip
    del _backend_module


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


def set_socket_nodelay(writer: "asyncio.StreamWriter") -> None:
    # input deltas are tiny and latency-critical.
    sock = writer.get_extra_info("socket")
    if sock is None:
        return
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except OSError:
        pass
