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

from pynput.mouse import Button


class MouseListener:
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


class MouseController:
    """Dummy controller"""

    def __init__(self):
        self._position = (-1, -1)

    def move(self, *args, **kwargs):
        # No operation
        return None

    def position(self, *args, **kwargs):
        # Return dummy position
        return None

    def press(self, *args, **kwargs):
        # No operation
        return None

    def release(self, *args, **kwargs):
        # No operation
        return None

    def click(self, *args, **kwargs):
        # No operation
        return None

    def scroll(self, *args, **kwargs):
        # No operation
        return None


__all__ = ["MouseListener", "MouseController", "Button"]
