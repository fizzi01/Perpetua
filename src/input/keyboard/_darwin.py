
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

from event.bus import EventBus
from network.stream.handler import StreamHandler

from . import _base


class ServerKeyboardListener(_base.ServerKeyboardListener):
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
            caps_lock = flags & Quartz.kCGEventFlagMaskAlphaShift

            media_volume_event = 14

            if caps_lock != 0:
                return event
            elif event_type == Quartz.kCGEventKeyDown:  # Key press event
                pass
            elif event_type == media_volume_event:
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
