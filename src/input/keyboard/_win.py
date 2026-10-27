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
        super().__init__(
            event_bus,
            stream_handler,
            command_stream,
            filtering,
        )

    # Windows emits a synthetic LCtrl (scan code 0x21D) together with AltGr.
    # Forwarding it makes clients see Ctrl+AltGr+<key>, which is a shortcut
    # on Linux/macOS instead of the AltGr character (e.g. "@", "~").
    _VK_LCONTROL = 0xA2
    _ALTGR_FAKE_CTRL_SCAN = 0x21D

    def _win32_suppress_filter(self, msg, data):
        if self._listener is not None:
            self._listener._suppress = self._listening
        if (
            data.vkCode == self._VK_LCONTROL
            and data.scanCode == self._ALTGR_FAKE_CTRL_SCAN
        ):
            # Still suppressed while forwarding, just not sent to the client.
            return False


class ClientKeyboardController(_base.ClientKeyboardController):
    def __init__(
        self,
        event_bus: EventBus,
        stream_handler: StreamHandler,
        command_stream: StreamHandler,
    ):
        super().__init__(event_bus, stream_handler, command_stream)
