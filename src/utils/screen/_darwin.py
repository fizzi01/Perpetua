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

from Quartz import (  # ty:ignore[unresolved-import]
    CGDisplayBounds,
    CGGetActiveDisplayList,
    CGMainDisplayID,
    CGSessionCopyCurrentDictionary,
)
from AppKit import (
    NSBundle,  # ty:ignore[unresolved-import]
)

from . import _base
from ._monitor import MonitorInfo


class Screen(_base.Screen):
    @classmethod
    def get_size(cls) -> tuple[int, int]:
        """
        Returns the size of the primary screen as a tuple (width, height).
        """
        mainMonitor = CGDisplayBounds(CGMainDisplayID())
        return mainMonitor.size.width, mainMonitor.size.height

    @classmethod
    def _enumerate_displays(cls) -> "list[MonitorInfo] | None":
        """Return per-display :class:`MonitorInfo` or ``None`` on failure."""
        try:
            # CGGetActiveDisplayList returns (err, ids_array, count). The
            # pyobjc binding pre-allocates the array — request up to 16
            # displays which is well past realistic limits.
            _, ids, count = CGGetActiveDisplayList(16, None, None)
        except Exception:
            return None
        if not count:
            return None

        try:
            main_id = CGMainDisplayID()
        except Exception:
            main_id = None

        result: list[MonitorInfo] = []
        for i in range(count):
            display_id = ids[i]
            bounds = CGDisplayBounds(display_id)
            ox = int(bounds.origin.x)
            oy = int(bounds.origin.y)
            w = int(bounds.size.width)
            h = int(bounds.size.height)
            result.append(
                MonitorInfo(
                    monitor_id=int(display_id),
                    min_x=ox,
                    min_y=oy,
                    max_x=ox + w,
                    max_y=oy + h,
                    is_primary=(display_id == main_id),
                )
            )
        return result

    @classmethod
    def get_virtual_bbox(cls) -> tuple[int, int, int, int]:
        """
        Union of every active display's bounds.

        On macOS each display has its own origin in the global coordinate
        space (the primary sits at (0, 0); secondary displays are placed
        relative to it via System Settings → Displays → Arrangement).
        """
        monitors = cls._enumerate_displays()
        if not monitors:
            return super().get_virtual_bbox()
        min_x = min(m.min_x for m in monitors)
        min_y = min(m.min_y for m in monitors)
        max_x = max(m.max_x for m in monitors)
        max_y = max(m.max_y for m in monitors)
        return min_x, min_y, max_x, max_y

    @classmethod
    def get_monitors(cls) -> list[MonitorInfo]:
        monitors = cls._enumerate_displays()
        if not monitors:
            return super().get_monitors()
        return monitors

    @classmethod
    def is_screen_locked(cls) -> bool:
        """
        Checks if the screen is currently locked.
        """
        d = CGSessionCopyCurrentDictionary()
        return (
            d.get("CGSSessionScreenIsLocked") and d.get("CGSSessionScreenIsLocked") == 1
        )

    @classmethod
    def hide_icon(cls):
        """
        Hides the application icon from the dock.
        Modify directly the bundle info dict, because wx.App will read it
        """
        info = NSBundle.mainBundle().infoDictionary()
        if not info.get("LSUIElement", False):
            info["LSUIElement"] = "1"
