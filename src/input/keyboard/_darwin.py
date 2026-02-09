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

    def _darwin_suppress_filter(self, event_type, event):
        if self._listening:
            flags = Quartz.CGEventGetFlags(event)

            # Handle Caps Lock state
            key_code = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)
            key = KeyUtilities.map_vk(key_code)
            key = KeyUtilities.map_to_key(key)
            caps_lock = flags & Quartz.kCGEventFlagMaskAlphaShift
            if caps_lock != 0 and not key == Key.caps_lock:
                caps_lock = 0

            if caps_lock != 0 and not self._caps_lock_state:
                return event
            elif event_type == Quartz.kCGEventKeyDown:  # Key press event
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
