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

import inspect
from typing import Optional
from pynput.keyboard import Key, KeyCode
import enum
from collections import deque
from typing import Callable

from model.client import ScreenPosition


class ButtonMapping(enum.Enum):
    """Cross-platform mouse buttons remapped to a stable set."""

    unknown = 0
    left = 1
    middle = 2
    right = 3


class ScreenEdge(enum.Enum):
    LEFT = 1
    RIGHT = 2
    TOP = 3
    BOTTOM = 4


def _as_bbox(screen_size: tuple) -> tuple[int, int, int, int]:
    """Normalize ``(w, h)`` or ``(min_x, min_y, max_x, max_y)`` to a bbox."""
    if len(screen_size) == 4:
        return (
            int(screen_size[0]),
            int(screen_size[1]),
            int(screen_size[2]),
            int(screen_size[3]),
        )
    return 0, 0, int(screen_size[0]), int(screen_size[1])


def _check_direction(
    movement_history,
    axis: int,
    sign: int,
    direction_ratio: float,
) -> bool:
    """``True`` if recent movement consistently points along ``axis`` in ``sign``."""
    pairs = len(movement_history) - 1
    if pairs < 1:
        return False
    min_agreements = int(pairs * direction_ratio)
    agreements = 0
    for i in range(pairs):
        if (movement_history[i + 1][axis] - movement_history[i][axis]) * sign > 0:
            agreements += 1
    return agreements >= min_agreements


class EdgeDetector:
    """Detects when the cursor reaches the edge of the screen.

    ``screen_size`` may be a :class:`utils.screen.MonitorLayout` (per-
    monitor outer-edge detection with neighbour awareness), a bbox
    ``(min_x, min_y, max_x, max_y)``, or the legacy ``(w, h)``.
    """

    @staticmethod
    def clamp_to_screen(
        x: float | int, y: float | int, screen_size: tuple
    ) -> tuple[float, float]:
        """Clamp ``(x, y)`` inside ``screen_size`` (``(w, h)`` or bbox)."""
        min_x, min_y, max_x, max_y = _as_bbox(screen_size)
        clamped_x = max(min_x, min(x, max_x - 1))
        clamped_y = max(min_y, min(y, max_y - 1))
        return clamped_x, clamped_y

    @staticmethod
    def is_at_edge(
        movement_history: deque | list,
        x: float | int,
        y: float | int,
        screen_size,
        is_dragging: bool,
        direction_ratio: float = 0.85,
    ) -> Optional[ScreenEdge]:
        """Return the edge the cursor is heading into, if any.

        With a :class:`MonitorLayout` an edge "counts" only if no
        neighbouring monitor abuts it at the cursor's secondary
        coordinate — fixes asymmetric layouts where the primary
        monitor's edges are interior to the union bbox.
        """
        if is_dragging:
            return None

        size = len(movement_history)
        if size < 2:
            return None

        if hasattr(screen_size, "monitors") and hasattr(screen_size, "find_monitor_at"):
            return EdgeDetector._is_at_edge_layout(
                movement_history,
                x,
                y,
                screen_size,
                direction_ratio,
            )

        min_x, min_y, max_x, max_y = _as_bbox(screen_size)

        x_edge = None
        x_axis_sign = 0
        if x <= min_x:
            x_edge = ScreenEdge.LEFT
            x_axis_sign = -1
        elif x >= max_x - 1:
            x_edge = ScreenEdge.RIGHT
            x_axis_sign = 1

        y_edge = None
        y_axis_sign = 0
        if y <= min_y:
            y_edge = ScreenEdge.TOP
            y_axis_sign = -1
        elif y >= max_y - 1:
            y_edge = ScreenEdge.BOTTOM
            y_axis_sign = 1

        if x_edge is None and y_edge is None:
            return None

        # Direction check with jitter tolerance
        pairs = size - 1
        min_agreements = int(pairs * direction_ratio)
        hist = movement_history

        # Check x-axis edge first (LEFT/RIGHT)
        if x_edge is not None:
            agreements = 0
            for i in range(pairs):
                if (hist[i + 1][0] - hist[i][0]) * x_axis_sign > 0:
                    agreements += 1
            if agreements >= min_agreements:
                return x_edge

        # Check y-axis edge (TOP/BOTTOM)
        if y_edge is not None:
            agreements = 0
            for i in range(pairs):
                if (hist[i + 1][1] - hist[i][1]) * y_axis_sign > 0:
                    agreements += 1
            if agreements >= min_agreements:
                return y_edge

        return None

    @staticmethod
    def _is_at_edge_layout(
        movement_history,
        x: float | int,
        y: float | int,
        layout,
        direction_ratio: float,
    ) -> Optional[ScreenEdge]:
        """Per-monitor variant of :meth:`is_at_edge` for MonitorLayout."""
        # Cursor may sit in a dead zone (L-shaped layout) or just shy of
        # a monitor edge — snap to the closest monitor.
        monitor = layout.find_monitor_at(x, y)
        if monitor is None:
            best = None
            best_dist = None
            for m in layout.monitors:
                cx = max(m.min_x, min(x, m.max_x - 1))
                cy = max(m.min_y, min(y, m.max_y - 1))
                dist = (cx - x) ** 2 + (cy - y) ** 2
                if best_dist is None or dist < best_dist:
                    best = m
                    best_dist = dist
            monitor = best
        if monitor is None:
            return None

        # Candidate edge = past the monitor's bound AND no neighbour
        # in that direction at the orthogonal coordinate.
        x_edge = None
        x_axis_sign = 0
        if x <= monitor.min_x and not layout.has_neighbor_left(monitor, y):
            x_edge = ScreenEdge.LEFT
            x_axis_sign = -1
        elif x >= monitor.max_x - 1 and not layout.has_neighbor_right(monitor, y):
            x_edge = ScreenEdge.RIGHT
            x_axis_sign = 1

        y_edge = None
        y_axis_sign = 0
        if y <= monitor.min_y and not layout.has_neighbor_top(monitor, x):
            y_edge = ScreenEdge.TOP
            y_axis_sign = -1
        elif y >= monitor.max_y - 1 and not layout.has_neighbor_bottom(monitor, x):
            y_edge = ScreenEdge.BOTTOM
            y_axis_sign = 1

        if x_edge is None and y_edge is None:
            return None

        if x_edge is not None and _check_direction(
            movement_history, 0, x_axis_sign, direction_ratio
        ):
            return x_edge
        if y_edge is not None and _check_direction(
            movement_history, 1, y_axis_sign, direction_ratio
        ):
            return y_edge
        return None

    def detect_edge(
        self,
        movement_history: deque | list,
        x: float | int,
        y: float | int,
        screen_size: tuple,
        is_dragging: bool,
        callbacks: dict[ScreenEdge, Callable],
    ):
        """Invoke the matching callback when the cursor hits a screen edge."""
        edge = self.is_at_edge(movement_history, x, y, screen_size, is_dragging)
        if edge and edge in callbacks:
            callbacks[edge]()

    @staticmethod
    def get_crossing_coords(
        x: float | int,
        y: float | int,
        screen_size: tuple,
        edge: ScreenEdge,
        screen: str | None,
    ) -> tuple[float, float]:
        """Coordinates for the cursor landing on the server after a crossing.

        The pinned axis flips to the opposite side; the free axis is
        normalized over the virtual-desktop bbox so the landing point is
        proportional to the source position.
        """
        if screen == "" or screen is None:
            return -1, -1

        min_x, min_y, max_x, max_y = _as_bbox(screen_size)
        width = max(1, max_x - min_x)
        height = max(1, max_y - min_y)
        x_norm = (x - min_x) / width
        y_norm = (y - min_y) / height

        if edge == ScreenEdge.BOTTOM and screen == ScreenPosition.TOP:
            return x_norm, 0.0
        elif edge == ScreenEdge.TOP and screen == ScreenPosition.BOTTOM:
            return x_norm, 1.0
        elif edge == ScreenEdge.LEFT and screen == ScreenPosition.RIGHT:
            return 1.0, y_norm
        elif edge == ScreenEdge.RIGHT and screen == ScreenPosition.LEFT:
            return 0.0, y_norm
        else:
            return -1, -1


class KeyUtilities:
    """Cross-platform keyboard key conversions."""

    @staticmethod
    def map_key(key: str) -> Key | KeyCode | None:
        """Map a string key name to a pynput ``Key`` or ``KeyCode``."""
        try:
            return Key[key]
        except KeyError:
            pass

        if key.startswith("vk_"):
            try:
                vk_code = int(key[3:])
                return KeyCode.from_vk(vk_code)
            except ValueError:
                pass

        try:
            return KeyCode.from_char(key)
        except Exception:
            pass

        return None

    @staticmethod
    def map_vk(vk_code: int) -> KeyCode:
        return KeyCode.from_vk(vk_code)

    @staticmethod
    def map_to_key(kc: KeyCode) -> Key | None:
        try:
            return Key(kc)
        except (KeyError, AttributeError, ValueError):
            return None

    @staticmethod
    def is_special(
        key: Key | KeyCode | None, filter_out: Optional[list[Key]] = None
    ) -> bool:
        """``True`` if ``key`` is a pynput special ``Key`` and not in ``filter_out``."""
        if filter_out and key in filter_out:
            return False

        return isinstance(key, Key)


def _wrap(f, args):
    """Wrap ``f`` to accept exactly ``args`` arguments (no-op when ``f`` is None)."""
    if f is None:
        return lambda *a: None
    else:
        argspec = inspect.getfullargspec(f)
        actual = len(inspect.signature(f).parameters)
        defaults = len(argspec.defaults) if argspec.defaults else 0
        if actual - defaults > args:
            raise ValueError(f)
        elif actual >= args or argspec.varargs is not None:
            return f
        else:
            return lambda *a: f(*a[:actual])
