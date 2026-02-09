"""
Provides mouse input support for macOS (Darwin) systems.
"""


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

import Quartz
from AppKit import (
    NSEventTypeGesture,  # ty:ignore[unresolved-import]
    NSEventTypeBeginGesture,  # ty:ignore[unresolved-import]
    NSEventTypeSwipe,  # ty:ignore[unresolved-import]
    NSEventTypeRotate,  # ty:ignore[unresolved-import]
    NSEventTypeMagnify,  # ty:ignore[unresolved-import]
)


from . import _base
from ._base import Logger


def _no_suppress_filter(event_type, event):
    return event


class ServerMouseListener(_base.ServerMouseListener):
    """
    It listens for mouse events on macOS systems.
    Its main purpose is to capture mouse movements and clicks. And handle some border cases like cursor reaching screen edges.
    """

    def _darwin_mouse_suppress_filter(self, event_type, event):
        if self._listening:
            gesture_events = []
            # Tenta di aggiungere le costanti degli eventi gestuali
            try:
                gesture_events.extend(
                    [
                        NSEventTypeGesture,
                        NSEventTypeMagnify,
                        NSEventTypeSwipe,
                        NSEventTypeRotate,
                        NSEventTypeBeginGesture,
                    ]
                )
            except AttributeError:
                pass

            # Aggiungi i valori numerici per le costanti mancanti
            gesture_events.extend(
                [
                    29,  # kCGEventGesture
                ]
            )

            if (
                event_type
                in [
                    Quartz.kCGEventLeftMouseDown,
                    Quartz.kCGEventRightMouseDown,
                    Quartz.kCGEventOtherMouseDown,
                    Quartz.kCGEventLeftMouseDragged,
                    Quartz.kCGEventRightMouseDragged,
                    Quartz.kCGEventOtherMouseDragged,
                    Quartz.kCGEventScrollWheel,
                ]
                + gesture_events
            ):
                pass
            else:
                return event
        else:
            return event


class ServerMouseController(_base.ServerMouseController):
    """
    It controls the mouse on macOS systems.
    Its main purpose is to move the cursor and simulate mouse clicks.
    """

    pass


class ClientMouseController(_base.ClientMouseController):
    """
    It controls the mouse on macOS systems.
    Its main purpose is to move the cursor and simulate mouse clicks.
    """

    def _move_cursor(
        self, x: float | int, y: float | int, dx: float | int, dy: float | int
    ):
        """
        Move the mouse cursor to the specified (x, y) coordinates.
        We redefine this method to properly handle movement after reaching edges,
        on macOS pynput will increase arbitrarily the coordinates without capping them to the screen size,
        so we need to handle this case by ourselves.
        """
        # if dx and dy are provided, use relative movement
        if x == -1 and y == -1:
            # Convert to int for pynput
            try:
                dx = int(dx)
                dy = int(dy)
            except ValueError:
                dx = 0
                dy = 0

            # CLAMPING
            # If current position is outside screen bounds, move only on the axis that is not out of bounds
            if self._controller.position[0] < 0 and dx < 0:
                dx = 0
            if self._controller.position[0] > self._screen_size[0] and dx > 0:
                dx = 0
            if self._controller.position[1] < 0 and dy < 0:
                dy = 0
            if self._controller.position[1] > self._screen_size[1] and dy > 0:
                dy = 0

            self._controller.move(dx=dx, dy=dy)
        else:
            try:
                # Denormalize coordinates by mapping into the client screen size
                x *= self._screen_size[0]
                y *= self._screen_size[1]
                x = int(x)
                y = int(y)
            except ValueError:
                return

            try:
                self._controller.position = (x, y)
            except Exception as e:
                # On some platforms, positioning may fail when cursor misses
                self._logger.log(f"Failed to position cursor -> {e}", Logger.ERROR)
