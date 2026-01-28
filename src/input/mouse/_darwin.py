"""
Provides mouse input support for macOS (Darwin) systems.
"""


#  Perpatua - open-source and cross-platform KVM software.
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
    NSEventTypeGesture,
    NSEventTypeBeginGesture,
    NSEventTypeSwipe,
    NSEventTypeRotate,
    NSEventTypeMagnify,
)


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

    pass
