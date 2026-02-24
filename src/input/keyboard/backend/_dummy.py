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

from pynput.keyboard import Key, KeyCode


class KeyboardListener:
    """Dummy listener"""

    def __init__(
        self,
        *args,
        **kwargs,
    ):
        self._running = False

    def start(self):
        # Dummy: do not spawn threads, keep not running
        self._running = False

    def stop(self):
        self._running = False

    def is_alive(self) -> bool:
        return False


class KeyboardController:
    """Dummy controller"""

    def press(self, key):
        # No operation
        return None

    def release(self, key):
        # No operation
        return None


__all__ = ["KeyboardListener", "KeyboardController", "Key", "KeyCode"]
