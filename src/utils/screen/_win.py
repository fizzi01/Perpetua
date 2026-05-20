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

from win32api import EnumDisplayMonitors, GetMonitorInfo, GetSystemMetrics

from . import _base
from ._monitor import MonitorInfo

# Cheaper than EnumDisplayMonitors when only the virtual-desktop union is needed.
_SM_XVIRTUALSCREEN = 76
_SM_YVIRTUALSCREEN = 77
_SM_CXVIRTUALSCREEN = 78
_SM_CYVIRTUALSCREEN = 79


class Screen(_base.Screen):
    @classmethod
    def get_size(cls) -> tuple[int, int]:
        width = GetSystemMetrics(0)
        height = GetSystemMetrics(1)
        return width, height

    # MONITORINFOEX dwFlags bit indicating the primary display.
    _MONITORINFOF_PRIMARY = 0x00000001

    @classmethod
    #todo: Recheck for monitor change
    def _enumerate_monitors(cls) -> "list[MonitorInfo] | None":
        """Per-monitor MonitorInfo via EnumDisplayMonitors. GetMonitorInfo
        adds primary flag and device name; fall back to bbox-only on failure."""
        try:
            monitors: list[MonitorInfo] = []
            for idx, (hmon, _hdc, rect) in enumerate(EnumDisplayMonitors()):
                left, top, right, bottom = rect
                is_primary = False
                name = ""
                try:
                    info = GetMonitorInfo(hmon)
                    flags = info.get("Flags", 0) if isinstance(info, dict) else 0
                    is_primary = bool(flags & cls._MONITORINFOF_PRIMARY)
                    name = str(info.get("Device", "")) if isinstance(info, dict) else ""
                except Exception:
                    pass
                monitors.append(
                    MonitorInfo(
                        monitor_id=int(hmon) if hmon else idx,
                        min_x=int(left),
                        min_y=int(top),
                        max_x=int(right),
                        max_y=int(bottom),
                        is_primary=is_primary,
                        name=name,
                    )
                )
            return monitors or None
        except Exception:
            return None

    @classmethod
    def get_virtual_bbox(cls) -> tuple[int, int, int, int]:
        """Virtual-desktop bbox. SM_*VIRTUALSCREEN is one syscall; fall back
        to EnumDisplayMonitors if those return zero."""
        try:
            x = GetSystemMetrics(_SM_XVIRTUALSCREEN)
            y = GetSystemMetrics(_SM_YVIRTUALSCREEN)
            w = GetSystemMetrics(_SM_CXVIRTUALSCREEN)
            h = GetSystemMetrics(_SM_CYVIRTUALSCREEN)
            if w > 0 and h > 0:
                return int(x), int(y), int(x + w), int(y + h)
        except Exception:
            pass

        monitors = cls._enumerate_monitors()
        if monitors:
            min_x = min(m.min_x for m in monitors)
            min_y = min(m.min_y for m in monitors)
            max_x = max(m.max_x for m in monitors)
            max_y = max(m.max_y for m in monitors)
            return min_x, min_y, max_x, max_y

        return super().get_virtual_bbox()

    @classmethod
    def get_monitors(cls) -> list[MonitorInfo]:
        monitors = cls._enumerate_monitors()
        if not monitors:
            return super().get_monitors()
        return monitors

    @classmethod
    def is_screen_locked(cls) -> bool:
        return False

    @classmethod
    def hide_icon(cls):
        pass
