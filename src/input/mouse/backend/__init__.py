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

MouseListener = None
MouseController = None
Button = None

# Unset environment variable to prevent pynput from loading the wrong backend
environ.pop("PYNPUT_BACKEND_MOUSE", None)
environ.pop("PYNPUT_BACKEND", None)

BACKEND: dict[str, str] = {}
# Import platform-specific mouse backends when available
if platform.startswith("linux"):
    # Check if we're running under Wayland or Xorg
    if (
        environ.get("XDG_SESSION_TYPE") == "wayland"
        or environ.get("WAYLAND_DISPLAY") is not None
    ):
        # Wayland backend (not implemented yet)
        print("Wayland backend not implemented yet, no mouse control available")
        from ._dummy import MouseListener, MouseController, Button

        BACKEND["mouse_listener"] = "dummy"
        BACKEND["mouse_controller"] = "dummy"
    else:
        # Fallback to xorg backend
        environ["PYNPUT_BACKEND_MOUSE"] = "xorg"
        BACKEND["mouse_listener"] = "xorg"
        BACKEND["mouse_controller"] = "xorg"

if not MouseListener or not MouseController or not Button:
    from pynput.mouse import Listener as MouseListener
    from pynput.mouse import Controller as MouseController
    from pynput.mouse import Button

if not BACKEND.get("mouse_listener") or not BACKEND.get("mouse_controller"):
    BACKEND["mouse_listener"] = "pynput"
    BACKEND["mouse_controller"] = "pynput"

__all__ = ["MouseListener", "MouseController", "Button", "BACKEND"]
