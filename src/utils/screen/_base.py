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

import time
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from ._monitor import MonitorInfo, MonitorLayout


# Short TTL cache for ``get_monitors()``. Several server-side hot paths
# call it back-to-back (handshake, layout edit, watch loop); the OS query
# is non-trivial. The watch loop invalidates the cache the moment it
# detects a genuine topology change so hot-plug latency stays at the
# polling cadence.
_MONITORS_CACHE_TTL = 2.0
_monitors_cache: Optional[tuple[float, list]] = None


class Screen:
    @classmethod
    def get_size(cls) -> tuple[int, int]:
        """Size of the primary screen as (width, height)."""
        raise NotImplementedError("Screen size retrieval not implemented for this OS.")

    @classmethod
    def get_size_str(cls) -> str:
        width, height = cls.get_size()
        return f"{width:.0f}x{height:.0f}"

    @classmethod
    def get_virtual_bbox(cls) -> tuple[int, int, int, int]:
        """Virtual-desktop bbox (min_x, min_y, max_x, max_y) across all monitors.
        Used by server-side coordinate normalization. Default degrades to the
        primary monitor; platform subclasses should override."""
        w, h = cls.get_size()
        return 0, 0, int(w), int(h)

    @classmethod
    def get_virtual_size(cls) -> tuple[int, int]:
        min_x, min_y, max_x, max_y = cls.get_virtual_bbox()
        return max_x - min_x, max_y - min_y

    @classmethod
    def get_monitors(cls) -> "list[MonitorInfo]":
        """Connected displays as MonitorInfo. Edge detection uses per-monitor
        bounds so asymmetric layouts can still cross at interior outer edges.
        Default returns the primary only; platform subclasses should override."""
        from ._monitor import MonitorInfo

        w, h = cls.get_size()
        return [
            MonitorInfo(
                monitor_id=0,
                min_x=0,
                min_y=0,
                max_x=int(w),
                max_y=int(h),
                is_primary=True,
            )
        ]

    @classmethod
    def get_monitors_cached(cls) -> "list[MonitorInfo]":
        """Cached wrapper around ``get_monitors`` (TTL ``_MONITORS_CACHE_TTL``).

        Hot paths (handshake, layout edit, etc.) should prefer this over
        ``get_monitors()``. The monitor-watch loop calls
        ``invalidate_monitors_cache()`` whenever it detects a real
        change so hot-plug latency stays at the polling cadence.
        """
        global _monitors_cache
        cached = _monitors_cache
        if cached is not None:
            ts, data = cached
            if time.monotonic() - ts < _MONITORS_CACHE_TTL:
                return list(data)
        fresh = cls.get_monitors()
        _monitors_cache = (time.monotonic(), list(fresh))
        return fresh

    @classmethod
    def get_monitor_layout(cls) -> "MonitorLayout":
        from ._monitor import MonitorLayout

        return MonitorLayout(monitors=tuple(cls.get_monitors()))

    @classmethod
    def is_screen_locked(cls) -> bool:
        raise NotImplementedError(
            "Screen lock status check not implemented for this OS."
        )

    @classmethod
    def hide_icon(cls):
        raise NotImplementedError("Icon hiding not implemented for this OS.")


def invalidate_monitors_cache() -> None:
    """Drop the cached ``Screen.get_monitors()`` result.

    Call this when a real topology change is detected (the monitor watch
    loop does) so the next ``get_monitors()`` re-queries the OS.
    """
    global _monitors_cache
    _monitors_cache = None
