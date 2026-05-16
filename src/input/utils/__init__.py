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
    """The various buttons.

    The actual values for these items differ between platforms. Some
    platforms may have additional buttons, but these are guaranteed to be
    present everywhere and we remap them to these values.
    """

    #: An unknown button was pressed
    unknown = 0

    #: The left button
    left = 1

    #: The middle button
    middle = 2

    #: The right button
    right = 3


class ScreenEdge(enum.Enum):
    LEFT = 1
    RIGHT = 2
    TOP = 3
    BOTTOM = 4


def _as_bbox(screen_size: tuple) -> tuple[int, int, int, int]:
    """Normalize the ``screen_size`` argument to a bbox tuple.

    Accepts either:
        * ``(width, height)`` — legacy single-monitor shape; treated as a
          bbox at origin ``(0, 0)``.
        * ``(min_x, min_y, max_x, max_y)`` — virtual-desktop bbox spanning
          every connected monitor.

    Internal helper kept private so callers don't accidentally branch on
    tuple length themselves.
    """
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
    """``True`` if the cursor's recent movement consistently points along
    ``axis`` (``0`` for X, ``1`` for Y) in ``sign`` direction.

    Mirrors the agreement check previously inlined in ``is_at_edge``.
    """
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
    """
    A utility class for detecting when the mouse cursor reaches the edges of the screen.

    Accepts three flavors of geometry argument (in order of preference):

    1. :class:`utils.screen.MonitorLayout` — per-monitor outer-edge
       detection with neighbor awareness. The right choice for
       multi-monitor servers / clients.
    2. ``(min_x, min_y, max_x, max_y)`` — virtual-desktop bbox; treats
       the union rect as one big monitor. OK for single-monitor or
       perfectly aligned multi-monitor layouts.
    3. ``(w, h)`` — legacy single-monitor shape.
    """

    @staticmethod
    def clamp_to_screen(
        x: float | int, y: float | int, screen_size: tuple
    ) -> tuple[float, float]:
        """
        Clamps the given (x, y) coordinates to be within the bounds of the screen.

        Accepts either ``(w, h)`` or the multi-monitor
        ``(min_x, min_y, max_x, max_y)`` bbox.
        """
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
        """
        Determines if the cursor is moving towards and has reached any edge of
        the screen.

        ``screen_size`` may be a :class:`MonitorLayout`, a bbox tuple
        ``(min_x, min_y, max_x, max_y)`` or the legacy ``(w, h)``. When a
        layout is provided, edges are checked against the OUTER edges of
        the monitor currently under the cursor (an edge "counts" only if
        no neighbouring monitor abuts it at the cursor's secondary
        coordinate — this fixes asymmetric layouts where the primary
        monitor's edges are interior to the union bbox).
        """
        if is_dragging:
            return None

        size = len(movement_history)
        if size < 2:
            return None

        # MonitorLayout path: per-monitor outer-edge detection.
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
        """Per-monitor outer-edge detection used by the
        :class:`MonitorLayout` path of :meth:`is_at_edge`.

        Locates the monitor under the cursor, then checks each side of
        that monitor as an outer edge only when no neighbouring monitor
        abuts it at the cursor's secondary coordinate. This unblocks
        asymmetric layouts where, e.g., the primary monitor's right edge
        is interior to the virtual desktop bbox but should still trigger
        a crossing because nothing sits to its right at this Y.
        """
        # Snap the cursor inside SOME monitor: if the cursor is in a
        # dead zone (L-shaped layout) or just shy of a monitor's edge,
        # pick the closest one. We try contains() first for the common
        # case.
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

        # Candidate edges: cursor at or past the monitor's bound AND no
        # neighbour in that direction at the orthogonal coordinate.
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
        """
        Detects if the cursor is at the edge and invokes the appropriate callback.

        Args:
            movement_history (deque | list): A deque or list of recent (x, y) positions of the cursor.
            x (float | int): Current x position of the cursor.
            y (float | int): Current y position of the cursor.
            screen_size (tuple): A tuple representing the screen size (width, height).
            is_dragging (bool): Whether the user is currently dragging (holding a button).
        """
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
        """
        Get the coordinates when crossing back from client to server.
        Coords will be the opposite of the real one (so opposite to the edge reached).

        ``screen_size`` accepts either ``(w, h)`` or the multi-monitor bbox
        ``(min_x, min_y, max_x, max_y)``. The non-pinned coordinate is
        normalized over the bbox span so the cross-screen position lands
        proportionally on the destination side of the virtual desktop.
        """
        if screen == "" or screen is None:
            return -1, -1

        min_x, min_y, max_x, max_y = _as_bbox(screen_size)
        width = max(1, max_x - min_x)
        height = max(1, max_y - min_y)
        x_norm = (x - min_x) / width
        y_norm = (y - min_y) / height

        # If we reach the bottom edge, we need to set y to 1 (top of the server screen)
        if edge == ScreenEdge.BOTTOM and screen == ScreenPosition.TOP:
            return x_norm, 0.0
        # If we reach the top edge, we need to set y to 0 (bottom of the server screen)
        elif edge == ScreenEdge.TOP and screen == ScreenPosition.BOTTOM:
            return x_norm, 1.0
        # If we reach the left edge, we need to set x to 1 (right of the server screen)
        elif edge == ScreenEdge.LEFT and screen == ScreenPosition.RIGHT:
            return 1.0, y_norm
        # If we reach the right edge, we need to set x to 0 (left of the server screen)
        elif edge == ScreenEdge.RIGHT and screen == ScreenPosition.LEFT:
            return 0.0, y_norm
        else:
            return -1, -1


class KeyUtilities:
    """
    This class provides utility functions for keyboard key conversions.
    Like mapping key names from different OS into a specific os.
    """

    @staticmethod
    def map_key(key: str) -> Key | KeyCode | None:
        """
        For pynpuy Key are all special keys, and KeyCode are all character keys.
        """
        # First check if key is a special key in pynput
        try:
            special = Key[key]
            return special
        except KeyError:
            pass

        # Check if it's a vk_ key
        if key.startswith("vk_"):
            try:
                vk_code = int(key[3:])
                return KeyCode.from_vk(vk_code)
            except ValueError:
                pass

        # Next check if it's a single character (KeyCode)
        try:
            return KeyCode.from_char(key)
        except Exception:
            pass

        # Otherwise return the original string (unmapped)
        return None

    @staticmethod
    def map_vk(vk_code: int) -> KeyCode:
        """
        Maps a virtual key code to a pynput KeyCode.
        """
        return KeyCode.from_vk(vk_code)

    @staticmethod
    def map_to_key(kc: KeyCode) -> Key | None:
        """
        Maps a pynput KeyCode to a Key if possible, otherwise returns None.
        """
        try:
            return Key(kc)
        except (KeyError, AttributeError, ValueError):
            return None

    @staticmethod
    def is_special(
        key: Key | KeyCode | None, filter_out: Optional[list[Key]] = None
    ) -> bool:
        """
        Check if the given key is a special key (pynput Key) or a character key (KeyCode).
        Args:
            key (Key | KeyCode | None): The key to check.
            filter_out (Optional[list[Key]]): List of keys to filter out from being considered special.
        Returns:
            bool: True if the key is a special key and not in filter_out, False otherwise
        """
        if filter_out and key in filter_out:
            return False

        return isinstance(key, Key)


def _wrap(f, args):
    """Wraps a callable to make it accept ``args`` number of arguments.

    :param f: The callable to wrap. If this is ``None`` a no-op wrapper is
        returned.

    :param int args: The number of arguments to accept.

    :raises ValueError: if f requires more than ``args`` arguments
    """
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
