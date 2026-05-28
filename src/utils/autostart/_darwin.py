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

"""macOS autostart via per-user LaunchAgent plist.

The plist is parked under ``~/Library/LaunchAgents`` and loaded via
``launchctl bootstrap gui/<uid>`` — the modern domain-aware command.
``launchctl load`` is kept as a graceful fallback for macOS < 10.10 / cases
where bootstrap fails (e.g. plist already loaded).
"""

import os
import plistlib
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional

from ._base import AutostartManager, AutostartStatus


LABEL = "com.federicoizzi.Perpetua"
DEFAULT_ARGS: List[str] = ["--start-minimized"]


class _MacAutostartManager(AutostartManager):
    @property
    def _plist_path(self) -> Path:
        return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"

    def is_enabled(self) -> AutostartStatus:
        p = self._plist_path
        if not p.is_file():
            return AutostartStatus(enabled=False)
        try:
            with p.open("rb") as f:
                data = plistlib.load(f)
            args = data.get("ProgramArguments") or []
            exec_path = args[0] if args else data.get("Program")
            return AutostartStatus(enabled=True, exec_path=exec_path)
        except (plistlib.InvalidFileException, OSError):
            return AutostartStatus(enabled=False)

    def enable(self, exec_path: str, args: Optional[List[str]] = None) -> None:
        if not os.path.isabs(exec_path):
            raise ValueError(f"exec_path must be absolute, got: {exec_path}")
        plist = {
            "Label": LABEL,
            "ProgramArguments": [
                exec_path,
                *(args if args is not None else DEFAULT_ARGS),
            ],
            "RunAtLoad": True,
            "KeepAlive": False,
            # Restrict the LaunchAgent to graphical sessions so it doesn't
            # try to run during headless ssh logins.
            "LimitLoadToSessionType": "Aqua",
        }
        path = self._plist_path
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as f:
            plistlib.dump(plist, f)
        self._launchctl_bootstrap(path)

    def disable(self) -> None:
        path = self._plist_path
        if path.is_file():
            self._launchctl_bootout(path)
            try:
                path.unlink()
            except FileNotFoundError:
                pass

    # ------------------------------------------------------------------
    @staticmethod
    def _launchctl_bootstrap(plist_path: Path) -> None:
        if shutil.which("launchctl") is None:
            return
        uid = os.getuid()
        # ``bootstrap`` fails if already loaded; that's fine for our idempotent
        # contract, so suppress errors.
        subprocess.run(
            ["launchctl", "bootstrap", f"gui/{uid}", str(plist_path)],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    @staticmethod
    def _launchctl_bootout(plist_path: Path) -> None:
        if shutil.which("launchctl") is None:
            return
        uid = os.getuid()
        subprocess.run(
            ["launchctl", "bootout", f"gui/{uid}/{LABEL}"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # Legacy fallback for older macOS — ignored if bootstrap path worked.
        subprocess.run(
            ["launchctl", "unload", str(plist_path)],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


AutostartManager = _MacAutostartManager
