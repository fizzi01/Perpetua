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
    CGEventGetFlags,  # ty:ignore[unresolved-import]
    CGEventGetIntegerValueField,  # ty:ignore[unresolved-import]
    kCGKeyboardEventKeycode,  # ty:ignore[unresolved-import]
    NSEventModifierFlagCapsLock,  # ty:ignore[unresolved-import]
    kCGEventFlagMaskAlphaShift,  # ty:ignore[unresolved-import]
    kCGEventKeyDown,  # ty:ignore[unresolved-import]
)
from AppKit import NSEvent  # ty:ignore[unresolved-import]

from event.bus import EventBus
from network.stream.handler import StreamHandler

from . import _base
from ._base import KeyUtilities, Key


class ServerKeyboardListener(_base.ServerKeyboardListener):
    MEDIA_VOLUME_EVENT = 14

    def __init__(
        self,
        event_bus: EventBus,
        stream_handler: StreamHandler,
        command_stream: StreamHandler,
        filtering: bool = True,
    ):
        super().__init__(event_bus, stream_handler, command_stream, filtering)

    @staticmethod
    def _get_lock_state() -> bool:
        return NSEvent.modifierFlags() & NSEventModifierFlagCapsLock != 0

    def _darwin_suppress_filter(self, event_type, event):
        if self._listening:
            flags = CGEventGetFlags(event)

            # Handle Caps Lock state
            caps_lock = flags & kCGEventFlagMaskAlphaShift
            if caps_lock != 0 and not self._get_lock_state():
                return event
            elif event_type == kCGEventKeyDown:  # Key press event
                pass
            elif event_type == self.MEDIA_VOLUME_EVENT:
                pass
            else:
                return event
        else:
            return event


class ClientKeyboardController(_base.ClientKeyboardController):
    def __init__(
        self,
        event_bus: EventBus,
        stream_handler: StreamHandler,
        command_stream: StreamHandler,
    ):
        super().__init__(event_bus, stream_handler, command_stream)
