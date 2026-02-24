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

from sys import platform
from os import environ

# Import platform-specific mouse backends
if platform.startswith("linux"):
    # Check if we're running under Wayland or Xorg
    if (
        environ.get("XDG_SESSION_TYPE") == "wayland"
        or environ.get("WAYLAND_DISPLAY") is not None
    ):
        # Wayland backend (not implemented yet)
        print("Wayland backend not implemented yet, no keyboard control available")
        from ._dummy import KeyboardListener, KeyboardController, Key, KeyCode
    else:
        from ._xorg import KeyboardListener, Key, KeyCode
        from pynput.keyboard import Controller as KeyboardController
else:
    from pynput.keyboard import Listener as KeyboardListener
    from pynput.keyboard import Controller as KeyboardController
    from pynput.keyboard import Key, KeyCode

__all__ = ["KeyboardListener", "KeyboardController", "Key", "KeyCode"]
