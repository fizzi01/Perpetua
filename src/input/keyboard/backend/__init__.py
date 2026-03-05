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
from os import environ
from sys import platform

from pynput.keyboard import HotKey

# Unset environment variable to prevent pynput from loading the wrong backend
environ.pop("PYNPUT_BACKEND_MOUSE", None)
environ.pop("PYNPUT_BACKEND", None)

BACKEND: dict[str, str] = {}
# Import platform-specific mouse backends
if platform.startswith("linux"):
    from ._uinput import KeyboardListener, Key, KeyCode

    BACKEND["keyboard_listener"] = "uinput"

    # Check for wayland
    if (
        "WAYLAND_DISPLAY" in environ
        or "XDG_SESSION_TYPE" in environ
        and environ["XDG_SESSION_TYPE"] == "wayland"
    ):
        from ._uinput import KeyboardController

        BACKEND["keyboard_controller"] = "uinput"
    else:
        # Fallback to xorg backend by forcing environment variable for pynput
        environ["PYNPUT_BACKEND_KEYBOARD"] = "xorg"
        from pynput.keyboard import Controller as KeyboardController

        BACKEND["keyboard_controller"] = "xorg"
else:
    from pynput.keyboard import Listener as KeyboardListener
    from pynput.keyboard import Controller as KeyboardController
    from pynput.keyboard import Key, KeyCode

    BACKEND["keyboard_listener"] = "pynput"
    BACKEND["keyboard_controller"] = "pynput"

__all__ = [
    "KeyboardListener",
    "KeyboardController",
    "Key",
    "KeyCode",
    "HotKey",
    "BACKEND",
]
