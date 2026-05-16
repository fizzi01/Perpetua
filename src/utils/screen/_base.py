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

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._monitor import MonitorInfo, MonitorLayout


class Screen:
    @classmethod
    def get_size(cls) -> tuple[int, int]:
        """
        Returns the size of the primary screen as a tuple (width, height).
        """
        raise NotImplementedError("Screen size retrieval not implemented for this OS.")

    @classmethod
    def get_size_str(cls) -> str:
        """
        Returns the size of the primary screen as a string "widthxheight".
        """
        width, height = cls.get_size()
        return f"{width:.0f}x{height:.0f}"

    @classmethod
    def get_virtual_bbox(cls) -> tuple[int, int, int, int]:
        """
        Returns the bounding box ``(min_x, min_y, max_x, max_y)`` of the
        virtual desktop spanning every connected monitor.

        Used by coordinate normalization on the server side, so coordinates
        from any monitor map proportionally onto the multi-monitor layout
        (and not just the primary monitor).

        Default implementation degrades to ``(0, 0, w, h)`` from
        :meth:`get_size`; platform subclasses should override to enumerate
        all monitors.
        """
        w, h = cls.get_size()
        return 0, 0, int(w), int(h)

    @classmethod
    def get_virtual_size(cls) -> tuple[int, int]:
        """Return ``(width, height)`` of the virtual desktop bbox."""
        min_x, min_y, max_x, max_y = cls.get_virtual_bbox()
        return max_x - min_x, max_y - min_y

    @classmethod
    def get_monitors(cls) -> "list[MonitorInfo]":
        """
        Returns the list of connected displays as :class:`MonitorInfo`.

        Used by edge detection so a cursor on a non-primary monitor can
        still cross when it reaches an outer edge that happens to be
        INTERIOR to the union bbox (asymmetric layouts, e.g. wider
        monitor above a narrower primary). Each per-monitor edge is a
        crossing candidate only if no neighbouring monitor extends past
        it at the cursor's secondary coordinate.

        Default implementation returns a single-item list with the
        primary monitor's bounds (read from :meth:`get_size`); platform
        subclasses should override to enumerate all displays.
        """
        from ._monitor import MonitorInfo  # local import to avoid cycle

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
    def get_monitor_layout(cls) -> "MonitorLayout":
        """Return a :class:`MonitorLayout` aggregating every monitor."""
        from ._monitor import MonitorLayout  # local import to avoid cycle

        return MonitorLayout(monitors=tuple(cls.get_monitors()))

    @classmethod
    def is_screen_locked(cls) -> bool:
        """
        Checks if the screen is currently locked.
        """
        raise NotImplementedError(
            "Screen lock status check not implemented for this OS."
        )

    @classmethod
    def hide_icon(cls):
        """
        Hides the application icon from the taskbar/dock.
        """
        raise NotImplementedError("Icon hiding not implemented for this OS.")
