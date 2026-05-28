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
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class AutostartStatus:
    """Snapshot of the autostart entry state.

    ``exec_path`` is the executable currently registered (if any) — surfaced
    so the GUI can warn when a stale entry points at an old install location
    after an upgrade.
    """

    enabled: bool
    exec_path: Optional[str] = None


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
    def set_enabled(self, enabled: bool, exec_path: Optional[str] = None) -> None:
        """Idempotent enable/disable wrapper used by the daemon command."""
        if enabled:
            if not exec_path:
                raise ValueError("exec_path is required when enabling autostart")
            self.enable(exec_path)
        else:
            self.disable()
