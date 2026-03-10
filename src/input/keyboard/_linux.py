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
from model.client import ScreenPosition

from . import _base
from .backend import Key, KeyCode, HotKey


class ServerKeyboardListener(_base.ServerKeyboardListener):
    def __init__(
        self,
        event_bus: EventBus,
        stream_handler: StreamHandler,
        command_stream: StreamHandler,
        filtering: bool = True,
    ):
        super().__init__(event_bus, stream_handler, command_stream, filtering)

    def _canonical(self, key):
        if isinstance(key, Key):
            if key in self._MOD_MAP:
                return self._MOD_MAP[key]
            try:
                return KeyCode.from_vk(key.value.vk)
            except Exception:
                pass
        # Normalize character keys to lowercase so Shift+P matches 'p'
        if isinstance(key, KeyCode) and key.char is not None:
            lower = key.char.lower()
            if lower != key.char:
                return KeyCode(char=lower)
        return key

    def _build_hotkeys(self) -> list[HotKey]:
        # Build key sets manually using the canonical forms.
        def make_cb(coro_fn, *args):
            def cb():
                self._hotkey_consumed = True
                self._schedule_async(coro_fn(*args))

            return cb

        ctrl = Key.ctrl
        shift = Key.shift
        p = KeyCode(char="p")
        q = KeyCode(char="q")
        left = KeyCode.from_vk(Key.left.value.vk)
        right = KeyCode.from_vk(Key.right.value.vk)
        up = KeyCode.from_vk(Key.up.value.vk)
        down = KeyCode.from_vk(Key.down.value.vk)
        esc = KeyCode.from_vk(Key.esc.value.vk)

        entries = [
            (
                frozenset({ctrl, shift, p, left}),
                make_cb(self._hotkey_switch_screen, ScreenPosition.LEFT),
            ),
            (
                frozenset({ctrl, shift, p, right}),
                make_cb(self._hotkey_switch_screen, ScreenPosition.RIGHT),
            ),
            (
                frozenset({ctrl, shift, p, up}),
                make_cb(self._hotkey_switch_screen, ScreenPosition.TOP),
            ),
            (
                frozenset({ctrl, shift, p, down}),
                make_cb(self._hotkey_switch_screen, ScreenPosition.BOTTOM),
            ),
            (frozenset({ctrl, shift, p, esc}), make_cb(self._hotkey_switch_to_server)),
            (frozenset({ctrl, shift, q}), make_cb(self._hotkey_panic)),
        ]
        return [HotKey(keys, cb) for keys, cb in entries]

    def _xorg_suppress_filter(self, event):
        if self._listening:
            return False

        return event


class ClientKeyboardController(_base.ClientKeyboardController):
    def __init__(
        self,
        event_bus: EventBus,
        stream_handler: StreamHandler,
        command_stream: StreamHandler,
    ):
        super().__init__(event_bus, stream_handler, command_stream)
