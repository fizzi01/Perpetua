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
"""Multi-monitor data model.

instead of treating the virtual desktop as a single bbox,
each connected display is represented by a
:class:`MonitorInfo` carrying enough metadata to:

- detect per-monitor outer edges (a cursor at the right edge of monitor A
  triggers a crossing only if no other monitor abuts A on the right at
  that Y coordinate),
- denormalize incoming cursor positions onto a specific monitor #TODO
- expose a stable monitor identity so future GUI settings can let users
  customise the spatial arrangement (per-edge target client, custom
  scaling factors, alias names, etc.).

:class:`MonitorLayout` is the aggregate view used by the mouse listener
and edge detector. Today it is built automatically from
:meth:`Screen.get_monitors`; later the GUI can override its
``edge_routes`` to implement asymmetric / user-defined arrangements
without touching the input layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional


@dataclass(frozen=True)
class MonitorInfo:
    """Geometry + metadata for a single connected display.

    Coordinates are in the OS global display coordinate space (origin at
    the primary monitor's top-left on macOS / Windows; the X server root
    origin on Linux/X11).
    """

    monitor_id: int
    min_x: int
    min_y: int
    max_x: int
    max_y: int
    is_primary: bool = False
    name: str = ""
    scaling_factor: float = 1.0

    @property
    def width(self) -> int:
        return self.max_x - self.min_x

    @property
    def height(self) -> int:
        return self.max_y - self.min_y

    @property
    def bbox(self) -> tuple[int, int, int, int]:
        return self.min_x, self.min_y, self.max_x, self.max_y

    def contains(self, x: float, y: float) -> bool:
        """``True`` if ``(x, y)`` falls inside this monitor's bounds."""
        return self.min_x <= x < self.max_x and self.min_y <= y < self.max_y


@dataclass
class MonitorLayout:
    """Aggregate of the connected displays.

    ``monitors`` is the source of truth; ``virtual_bbox`` is a derived
    convenience for cases where a single union rect is enough.

    ``edge_routes`` is the placeholder hook for future user-configurable
    arrangements: it will map ``(monitor_id, edge_name)`` to a routing
    target (typically a :class:`model.client.ScreenPosition`). Today it
    is empty and edge routing falls back to the global
    ``ServerMouseListener._active_screens`` lookup; the field is reserved
    so the data shape is stable for downstream GUIs / config files.
    """

    monitors: tuple[MonitorInfo, ...] = field(default_factory=tuple)
    edge_routes: dict[tuple[int, str], str] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def from_bboxes(
        cls,
        bboxes: Iterable[tuple[int, int, int, int]],
        primary_index: Optional[int] = None,
    ) -> "MonitorLayout":
        """Build a layout from raw bbox tuples (no scaling/name info)."""
        monitors: list[MonitorInfo] = []
        for idx, (min_x, min_y, max_x, max_y) in enumerate(bboxes):
            monitors.append(
                MonitorInfo(
                    monitor_id=idx,
                    min_x=int(min_x),
                    min_y=int(min_y),
                    max_x=int(max_x),
                    max_y=int(max_y),
                    is_primary=(
                        idx == 0 if primary_index is None else idx == primary_index
                    ),
                )
            )
        return cls(monitors=tuple(monitors))

    # ------------------------------------------------------------------
    # Geometric helpers
    # ------------------------------------------------------------------

    @property
    def virtual_bbox(self) -> tuple[int, int, int, int]:
        """Union rect of every monitor (degenerate ``(0, 0, 0, 0)`` if
        the layout is empty)."""
        if not self.monitors:
            return 0, 0, 0, 0
        min_x = min(m.min_x for m in self.monitors)
        min_y = min(m.min_y for m in self.monitors)
        max_x = max(m.max_x for m in self.monitors)
        max_y = max(m.max_y for m in self.monitors)
        return min_x, min_y, max_x, max_y

    def find_monitor_at(self, x: float, y: float) -> Optional[MonitorInfo]:
        """Return the monitor containing ``(x, y)``, or ``None`` (dead zone)."""
        for m in self.monitors:
            if m.contains(x, y):
                return m
        return None

    # ------------------------------------------------------------------
    # Per-monitor outer-edge detection
    # ------------------------------------------------------------------

    def has_neighbor_left(self, monitor: MonitorInfo, y: float) -> bool:
        """``True`` if another monitor abuts ``monitor`` on its LEFT
        side at the given Y (i.e. moving further left would land on that
        neighbour, not into empty space)."""
        for m in self.monitors:
            if m.monitor_id == monitor.monitor_id:
                continue
            if m.max_x <= monitor.min_x and m.min_y <= y < m.max_y:
                # Allow a small "snap" tolerance so abutting edges count
                # as neighbours even with a 1-2 pixel gap.
                if monitor.min_x - m.max_x <= 2:
                    return True
        return False

    def has_neighbor_right(self, monitor: MonitorInfo, y: float) -> bool:
        for m in self.monitors:
            if m.monitor_id == monitor.monitor_id:
                continue
            if m.min_x >= monitor.max_x and m.min_y <= y < m.max_y:
                if m.min_x - monitor.max_x <= 2:
                    return True
        return False

    def has_neighbor_top(self, monitor: MonitorInfo, x: float) -> bool:
        for m in self.monitors:
            if m.monitor_id == monitor.monitor_id:
                continue
            if m.max_y <= monitor.min_y and m.min_x <= x < m.max_x:
                if monitor.min_y - m.max_y <= 2:
                    return True
        return False

    def has_neighbor_bottom(self, monitor: MonitorInfo, x: float) -> bool:
        for m in self.monitors:
            if m.monitor_id == monitor.monitor_id:
                continue
            if m.min_y >= monitor.max_y and m.min_x <= x < m.max_x:
                if m.min_y - monitor.max_y <= 2:
                    return True
        return False
