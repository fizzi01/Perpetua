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

from input._platform import (
    BackendRule,
    input_capture_available,
    is_linux,
    is_wayland,
    remote_desktop_available,
    resolve_backend,
)

_RULES = [
    # Wayland: libei through the XDG portals, each side on what the portal
    # offers rather than on the desktop's name.
    # Listener: InputCapture portal.
    BackendRule(
        condition=lambda: is_linux() and is_wayland() and input_capture_available(),
        module="._libei",
        symbols={"MouseListener": "MouseListener", "Button": "Button"},
        names={"mouse_listener": "libei"},
    ),
    # Controller: RemoteDesktop portal with ConnectToEIS.
    BackendRule(
        condition=lambda: is_linux() and is_wayland() and remote_desktop_available(),
        module="._libei",
        symbols={"MouseController": "MouseController", "Button": "Button"},
        names={"mouse_controller": "libei"},
    ),
    # Wayland: whatever the portal doesn't offer (dummy fallback)
    # TODO: add wlroots (zwlr_virtual_pointer).
    BackendRule(
        condition=lambda: is_linux() and is_wayland(),
        module="._dummy",
        symbols={
            "MouseListener": "MouseListener",
            "MouseController": "MouseController",
            "Button": "Button",
        },
        names={"mouse_listener": "dummy", "mouse_controller": "dummy"},
    ),
    # Linux + Xorg: pynput forced to xorg
    BackendRule(
        condition=lambda: is_linux() and not is_wayland(),
        module="pynput.mouse",
        symbols={
            "MouseListener": "Listener",
            "MouseController": "Controller",
            "Button": "Button",
        },
        names={"mouse_listener": "xorg", "mouse_controller": "xorg"},
        pynput_force=("mouse", "xorg"),
    ),
    # Default fallback: pynput
    BackendRule(
        condition=None,
        module="pynput.mouse",
        symbols={
            "MouseListener": "Listener",
            "MouseController": "Controller",
            "Button": "Button",
        },
        names={"mouse_listener": "os-specific", "mouse_controller": "os-specific"},
    ),
]

BACKEND, _symbols = resolve_backend(__name__, _RULES)

MouseListener = _symbols["MouseListener"]
MouseController = _symbols["MouseController"]
Button = _symbols["Button"]

__all__ = ["MouseListener", "MouseController", "Button", "BACKEND"]
