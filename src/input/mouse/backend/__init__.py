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

from src.input._platform import (
    BackendRule,
    is_linux,
    is_wayland,
    is_gnome,
    resolve_backend,
)

_RULES = [
    # Wayland: GNOME (libei via XDG Desktop Portal)
    # Listener dummy (not implemented yet), controller from libei.
    BackendRule(
        condition=lambda: is_linux() and is_wayland() and is_gnome(),
        module="._libei",
        symbols={"MouseController": "MouseController"},
        names={"mouse_controller": "libei"},
    ),
    BackendRule(
        condition=lambda: is_linux() and is_wayland() and is_gnome(),
        module="._dummy",
        symbols={
            "MouseListener": "MouseListener",
            "Button": "Button",
        },
        names={"mouse_listener": "dummy"},
    ),
    # Wayland: other compositors (uinput fallback)
    # TODO: add wlroots (zwlr_virtual_pointer) / KDE backends here.
    BackendRule(
        condition=lambda: is_linux() and is_wayland(),
        module="._uinput",
        symbols={
            "MouseListener": "MouseListener",
            "MouseController": "MouseController",
            "Button": "Button",
        },
        names={"mouse_listener": "uinput", "mouse_controller": "uinput"},
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
        names={"mouse_listener": "pynput", "mouse_controller": "pynput"},
    ),
]

BACKEND, _symbols = resolve_backend(__name__, _RULES)

MouseListener = _symbols["MouseListener"]
MouseController = _symbols["MouseController"]
Button = _symbols["Button"]

__all__ = ["MouseListener", "MouseController", "Button", "BACKEND"]
