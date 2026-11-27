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

from importlib import import_module
from os import environ
from sys import platform
from typing import Any, Callable, NamedTuple

from utils import (
    BACKEND_IMPORT_ERRORS,
    backend_failures,
    record_backend_failure,
    unavailable_symbol,
)


def is_linux() -> bool:
    return platform.startswith("linux")


def is_wayland() -> bool:
    return (
        environ.get("WAYLAND_DISPLAY") is not None
        or environ.get("XDG_SESSION_TYPE") == "wayland"
    )


def _current_desktop() -> str:
    """Return the lowercase XDG_CURRENT_DESKTOP value (first entry)."""
    raw = environ.get("XDG_CURRENT_DESKTOP", "")
    return raw.split(":")[0].strip().lower()


def is_gnome() -> bool:
    return _current_desktop() in ("gnome", "ubuntu", "pop")


def is_wlroots() -> bool:
    """Compositors based on wlroots (Sway, Hyprland, etc.)."""
    return _current_desktop() in ("sway", "hyprland", "wayfire", "river", "wlroots")


def is_kde() -> bool:
    return _current_desktop() in ("kde", "plasma")


def cursor_feedback_available() -> bool:
    """Whether this machine, as a client, can read its own cursor position.

    False on GNOME/KDE Wayland, where input goes through a libei sender that
    gets no position back: the server then tracks the cursor and sends
    absolute positions.
    """
    return not (is_linux() and is_wayland() and (is_gnome() or is_kde()))


class BackendRule(NamedTuple):
    """Declarative rule for backend resolution.

    condition:    callable returning bool, or None for unconditional match.
    module:       module path (relative requires package context).
    symbols:      maps exported name -> attribute name in the module.
    names:        maps BACKEND key -> backend name string.
    pynput_force: optional (component, backend) to set before importing.
    """

    condition: Callable[[], bool] | None
    module: str
    symbols: dict[str, str]
    names: dict[str, str]
    pynput_force: tuple[str, str] | None = None


def resolve_backend(
    package: str,
    rules: list[BackendRule],
) -> tuple[dict[str, str], dict[str, Any]]:
    """Resolve backend by evaluating rules in order.

    Returns (BACKEND dict, exported symbols dict).
    Each rule whose condition matches is applied, and its symbols are merged.
    The first rule to provide a given symbol wins.
    """
    # Clean pynput env before any import
    environ.pop("PYNPUT_BACKEND_MOUSE", None)
    environ.pop("PYNPUT_BACKEND_KEYBOARD", None)
    environ.pop("PYNPUT_BACKEND", None)

    backend: dict[str, str] = {}
    exports: dict[str, Any] = {}

    for rule in rules:
        if rule.condition is not None and not rule.condition():
            continue

        if rule.pynput_force:
            component, name = rule.pynput_force
            environ[f"PYNPUT_BACKEND_{component.upper()}"] = name

        try:
            mod = import_module(rule.module, package)
        except BACKEND_IMPORT_ERRORS as exc:
            # A backend import must never abort process start; see utils.
            record_backend_failure(package, rule.module, f"{type(exc).__name__}: {exc}")
            continue

        for export_name, attr_name in rule.symbols.items():
            exports.setdefault(export_name, getattr(mod, attr_name))

        for key, name in rule.names.items():
            backend.setdefault(key, name)

    # Symbols no surviving rule provided become stubs that raise on first use,
    # so the failure surfaces where the feature is exercised, not at import.
    for rule in rules:
        for export_name in rule.symbols:
            if export_name not in exports:
                exports[export_name] = _unavailable(package, export_name)
        for key in rule.names:
            backend.setdefault(key, "unavailable")

    return backend, exports


def _unavailable(package: str, symbol: str) -> type:
    """Build a placeholder for a symbol no backend could provide."""
    causes = "; ".join(f.error for f in backend_failures()) or "no rule matched"
    return unavailable_symbol(symbol, f"{package}.{symbol} is unavailable: {causes}")
