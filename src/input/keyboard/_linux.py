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
from ._base import Key, KeyCode, KeyboardController


class ServerKeyboardListener(_base.ServerKeyboardListener):
    def __init__(
        self,
        event_bus: EventBus,
        stream_handler: StreamHandler,
        command_stream: StreamHandler,
        filtering: bool = True,
    ):
        super().__init__(event_bus, stream_handler, command_stream, filtering)

        self._c = KeyboardController()

    def on_press(self, key: Key | KeyCode | None, inject: bool = False):
        """
        Callback for key press events.
        """
        if inject or key is None:
            return

        # Linux suppress logic
        if not self._listening:
            try:
                self._c.press(key.char)
            except AttributeError:
                self._c.press(key)
            return

        super().on_press(key)

    def on_release(self, key: Key | KeyCode | None, inject: bool = False):
        """
        Callback for key release events.
        """
        if inject or key is None:
            return

        # Linux suppress logic
        if not self._listening:
            try:
                self._c.release(key.char)
            except AttributeError:
                self._c.release(key)
            return

        super().on_release(key)


class ClientKeyboardController(_base.ClientKeyboardController):
    def __init__(
        self,
        event_bus: EventBus,
        stream_handler: StreamHandler,
        command_stream: StreamHandler,
    ):
        super().__init__(event_bus, stream_handler, command_stream)
