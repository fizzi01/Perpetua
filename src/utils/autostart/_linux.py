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

"""Linux autostart via XDG Desktop ``autostart/*.desktop`` entries.

Targets the XDG Autostart Specification, which is honoured by every major
desktop (GNOME / KDE / Xfce / MATE / Cinnamon / LXQt / Budgie). The
systemd-user-service path is a separate "advanced" toggle, not handled here.
"""

import os
import shlex
from pathlib import Path
from typing import List, Optional

from ._base import DEFAULT_ARGS, AutostartManager, AutostartStatus


def _xdg_autostart_dir() -> Path:
    config_home = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(config_home) / "autostart"


def _parse_exec_argv(line: str) -> List[str]:
    """Split a desktop-entry ``Exec=`` line into its argv list.

    The XDG spec allows ``%`` field codes (e.g. ``%U``, ``%F``); we don't emit
    any, so a plain ``shlex.split`` recovers ``[exec_path, *args]`` — the first
    element lets the GUI flag a stale entry, the rest carries the launch mode.
    """
    try:
        return shlex.split(line)
    except ValueError:
        return []


class _LinuxAutostartManager(AutostartManager):
    @property
    def _desktop_path(self) -> Path:
        return _xdg_autostart_dir() / f"{self.ENTRY_NAME}.desktop"

    def is_enabled(self) -> AutostartStatus:
        p = self._desktop_path
        if not p.is_file():
            return AutostartStatus(enabled=False)
        exec_path: Optional[str] = None
        args: List[str] = []
        hidden = False
        try:
            with p.open("r", encoding="utf-8") as f:
                for raw in f:
                    line = raw.strip()
                    if line.startswith("Exec="):
                        argv = _parse_exec_argv(line[len("Exec=") :])
                        if argv:
                            exec_path = argv[0]
                            args = argv[1:]
                    elif line.startswith("Hidden="):
                        # XDG: ``Hidden=true`` is the documented way to mark
                        # an entry as disabled without deleting it.
                        hidden = line[len("Hidden=") :].strip().lower() == "true"
        except OSError:
            return AutostartStatus(enabled=False)
        return AutostartStatus(enabled=not hidden, exec_path=exec_path, args=args)

    def enable(self, exec_path: str, args: Optional[List[str]] = None) -> None:
        if not os.path.isabs(exec_path):
            raise ValueError(f"exec_path must be absolute, got: {exec_path}")
        argv = [exec_path, *(args if args is not None else DEFAULT_ARGS)]
        # ``shlex.join`` quotes anything containing whitespace or special
        # chars per POSIX rules, which is what desktop-entry ``Exec=``
        # consumers expect.
        exec_line = shlex.join(argv)

        contents = (
            "[Desktop Entry]\n"
            "Type=Application\n"
            f"Name={self.APP_DISPLAY_NAME}\n"
            f"Comment={self.APP_DISPLAY_NAME} KVM (start at login)\n"
            f"Exec={exec_line}\n"
            "Terminal=false\n"
            "Categories=Network;Utility;\n"
            # Make sure GNOME and KDE both honour the entry.
            "X-GNOME-Autostart-enabled=true\n"
            "X-KDE-autostart-after=panel\n"
        )

        path = self._desktop_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")

    def disable(self) -> None:
        try:
            self._desktop_path.unlink()
        except FileNotFoundError:
            pass


AutostartManager = _LinuxAutostartManager
