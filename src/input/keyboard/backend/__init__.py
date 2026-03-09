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

from pynput.keyboard import HotKey

from src.input._platform import BackendRule, is_linux, is_wayland, resolve_backend

_RULES = [
    # Linux: listener + Key/KeyCode always from uinput
    BackendRule(
        condition=is_linux,
        module="._uinput",
        symbols={
            "KeyboardListener": "KeyboardListener",
            "Key": "Key",
            "KeyCode": "KeyCode",
        },
        names={"keyboard_listener": "uinput"},
    ),
    # Linux + Wayland: controller from uinput
    BackendRule(
        condition=lambda: is_linux() and is_wayland(),
        module="._uinput",
        symbols={"KeyboardController": "KeyboardController"},
        names={"keyboard_controller": "uinput"},
    ),
    # Linux + Xorg: controller from pynput forced to xorg
    BackendRule(
        condition=lambda: is_linux() and not is_wayland(),
        module="pynput.keyboard",
        symbols={"KeyboardController": "Controller"},
        names={"keyboard_controller": "xorg"},
        pynput_force=("keyboard", "xorg"),
    ),
    # Default fallback: everything from pynput
    BackendRule(
        condition=None,
        module="pynput.keyboard",
        symbols={
            "KeyboardListener": "Listener",
            "KeyboardController": "Controller",
            "Key": "Key",
            "KeyCode": "KeyCode",
        },
        names={
            "keyboard_listener": "os-specific",
            "keyboard_controller": "os-specific",
        },
    ),
]

BACKEND, _symbols = resolve_backend(__name__, _RULES)

KeyboardListener = _symbols["KeyboardListener"]
KeyboardController = _symbols["KeyboardController"]
Key = _symbols["Key"]
KeyCode = _symbols["KeyCode"]

__all__ = [
    "KeyboardListener",
    "KeyboardController",
    "Key",
    "KeyCode",
    "HotKey",
    "BACKEND",
]
