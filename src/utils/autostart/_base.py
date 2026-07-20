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

"""Per-OS autostart-at-login API.

A backend installs an autostart entry that runs the GUI with
``--start-minimized`` (or whatever ``exec_path`` + ``args`` the caller
supplies) at user login. Each backend is responsible for *one* mechanism:

* macOS: per-user LaunchAgent plist
* Linux: XDG ``autostart/*.desktop`` (works across GNOME/KDE/Xfce/MATE/...)
* Windows: ``HKCU\\...\\Run`` registry value

The systemd user-service path on Linux is intentionally **not** the default —
it's an "advanced" opt-in handled separately (the daemon ships the unit file
and the GUI exposes a toggle for it).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional

# The tray-launch flag: start the GUI hidden in the tray. Every autostart
# entry carries it; the optional service flag (``--server`` / ``--client``)
# is appended on top to pick the launch *mode* (see ``args_for_mode``).
DEFAULT_ARGS: List[str] = ["--start-minimized"]

# Autostart launch modes. ``off`` is represented by the absence of an entry
# (``disable()``); ``server`` / ``client`` append the matching service flag so
# the daemon auto-starts that service at login; ``plain`` just launches the app.
MODE_OFF = "off"
MODE_SERVER = "server"
MODE_CLIENT = "client"
MODE_PLAIN = "plain"


def args_for_mode(mode: Optional[str]) -> List[str]:
    """Build the launch args for a mode (``server`` / ``client`` / plain)."""
    args = list(DEFAULT_ARGS)
    if mode == MODE_SERVER:
        args.append("--server")
    elif mode == MODE_CLIENT:
        args.append("--client")
    return args


def mode_from_args(args: Optional[List[str]]) -> str:
    """Derive the launch mode from a registered entry's args.

    Returns ``server`` / ``client`` when the corresponding service flag is
    present, otherwise ``plain`` (an enabled entry that starts no service).
    """
    if args:
        if "--server" in args:
            return MODE_SERVER
        if "--client" in args:
            return MODE_CLIENT
    return MODE_PLAIN


@dataclass
class AutostartStatus:
    """Snapshot of the autostart entry state.

    ``exec_path`` is the executable currently registered (if any) — surfaced
    so the GUI can warn when a stale entry points at an old install location
    after an upgrade. ``args`` are the launch arguments parsed back from the
    entry; ``mode`` derives the launch mode (``off`` when disabled, otherwise
    ``server`` / ``client`` / ``plain``).
    """

    enabled: bool
    exec_path: Optional[str] = None
    args: List[str] = field(default_factory=list)

    @property
    def mode(self) -> str:
        if not self.enabled:
            return MODE_OFF
        return mode_from_args(self.args)


class AutostartManager(ABC):
    """Base class for per-OS autostart backends.

    The backend identifier (``ENTRY_NAME``) is a stable, lower-case slug used
    as the LaunchAgent label, the desktop-entry filename, and the registry
    value name. Keep it ASCII and free of separators (``com.perpetua.gui``
    style is constructed by ``_darwin``).
    """

    ENTRY_NAME = "perpetua"
    APP_DISPLAY_NAME = "Perpetua"

    @abstractmethod
    def is_enabled(self) -> AutostartStatus:
        """Return the current autostart entry, if any."""

    @abstractmethod
    def enable(self, exec_path: str, args: Optional[List[str]] = None) -> None:
        """Register the autostart entry. ``exec_path`` must be absolute.

        ``args`` defaults to ``["--start-minimized"]`` (the GUI's tray-launch
        flag); backends pass them through verbatim, so anything the launcher
        recognises is fair game.
        """

    @abstractmethod
    def disable(self) -> None:
        """Remove the autostart entry. No-op if it doesn't exist."""

    # Convenience -----------------------------------------------------------
    def set_enabled(
        self,
        enabled: bool,
        exec_path: Optional[str] = None,
        args: Optional[List[str]] = None,
    ) -> None:
        """Idempotent enable/disable wrapper used by the daemon command."""
        if enabled:
            if not exec_path:
                raise ValueError("exec_path is required when enabling autostart")
            self.enable(exec_path, args=args)
        else:
            self.disable()
