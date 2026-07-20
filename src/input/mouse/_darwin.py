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

from Quartz import (
    CGCursorIsVisible,  # ty:ignore[unresolved-import]
    CGEventCreateMouseEvent,  # ty:ignore[unresolved-import]
    CGEventPost,  # ty:ignore[unresolved-import]
    CGEventSetIntegerValueField,  # ty:ignore[unresolved-import]
    kCGEventLeftMouseDown,  # ty:ignore[unresolved-import]
    kCGEventRightMouseDown,  # ty:ignore[unresolved-import]
    kCGEventOtherMouseDown,  # ty:ignore[unresolved-import]
    kCGEventLeftMouseDragged,  # ty:ignore[unresolved-import]
    kCGEventRightMouseDragged,  # ty:ignore[unresolved-import]
    kCGEventOtherMouseDragged,  # ty:ignore[unresolved-import]
    kCGEventMouseMoved,  # ty:ignore[unresolved-import]
    kCGEventScrollWheel,  # ty:ignore[unresolved-import]
    kCGHIDEventTap,  # ty:ignore[unresolved-import]
    kCGMouseButtonLeft,  # ty:ignore[unresolved-import]
    kCGMouseButtonRight,  # ty:ignore[unresolved-import]
    kCGMouseEventDeltaX,  # ty:ignore[unresolved-import]
    kCGMouseEventDeltaY,  # ty:ignore[unresolved-import]
)
from AppKit import (
    NSEventTypeGesture,  # ty:ignore[unresolved-import]
    NSEventTypeBeginGesture,  # ty:ignore[unresolved-import]
    NSEventTypeSwipe,  # ty:ignore[unresolved-import]
    NSEventTypeRotate,  # ty:ignore[unresolved-import]
    NSEventTypeMagnify,  # ty:ignore[unresolved-import]
)


from input.utils import ButtonMapping

from . import _base


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
                    kCGEventLeftMouseDown,
                    kCGEventRightMouseDown,
                    kCGEventOtherMouseDown,
                    kCGEventLeftMouseDragged,
                    kCGEventRightMouseDragged,
                    kCGEventOtherMouseDragged,
                    kCGEventScrollWheel,
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

    def _cursor_is_hidden(self) -> bool:
        """True when the system cursor is hidden (game pointer lock).

        Read-only: a foreground game hides the cursor when it grabs the
        pointer. We never hide/show it ourselves.
        """
        return not CGCursorIsVisible()

    def _inject_relative(self, dx: int, dy: int) -> None:
        """Post a genuine relative-motion CGEvent so games read the delta.

        pynput's ``Controller.move`` warps the cursor to an absolute
        position; first-person games reading ``kCGMouseEventDeltaX/Y`` see
        nothing that way. Normally we move the system cursor to
        ``current + delta`` (so the visible pointer tracks on the desktop)
        *and* stamp the event's delta fields, which is what the game's camera
        consumes. Under a game pointer lock (``_pointer_locked``) the game
        pins/centers the cursor itself, so we keep the event at the *current*
        position — only the delta fields carry movement — otherwise our
        absolute point would drag the pinned cursor around. During a drag the
        motion must be delivered as a ``…MouseDragged`` event, not
        ``MouseMoved``, or the drag breaks.
        """
        try:
            cur_x, cur_y = self._controller.position
            if self._pointer_locked:
                new_x, new_y = cur_x, cur_y
            else:
                new_x = cur_x + dx
                new_y = cur_y + dy

            if self._pressed and self._is_dragging:
                if self._previous_button == ButtonMapping.right.value:
                    event_type = kCGEventRightMouseDragged
                    button = kCGMouseButtonRight
                else:
                    event_type = kCGEventLeftMouseDragged
                    button = kCGMouseButtonLeft
            else:
                event_type = kCGEventMouseMoved
                button = kCGMouseButtonLeft

            event = CGEventCreateMouseEvent(None, event_type, (new_x, new_y), button)
            CGEventSetIntegerValueField(event, kCGMouseEventDeltaX, int(dx))
            CGEventSetIntegerValueField(event, kCGMouseEventDeltaY, int(dy))
            CGEventPost(kCGHIDEventTap, event)
        except Exception as e:
            self._logger.error("relative CGEvent injection failed", error=str(e))
            super()._inject_relative(dx, dy)
