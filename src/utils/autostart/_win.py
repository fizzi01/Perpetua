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

"""Windows autostart via HKCU ``...\\CurrentVersion\\Run`` registry value.

We deliberately stay in ``HKEY_CURRENT_USER`` so we don't need elevation and
the entry follows the user across machines that roam this hive. ``HKLM``
would require admin rights and apply system-wide, which is the wrong scope
for a per-user GUI.
"""

import shlex
from typing import List, Optional

from ._base import AutostartManager, AutostartStatus

try:
    import winreg  # type: ignore[import]
except ImportError:  # pragma: no cover - non-Windows imports for typing only
    winreg = None  # type: ignore[assignment]


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "Perpetua"
DEFAULT_ARGS: List[str] = ["--start-minimized"]


def _format_command(exec_path: str, args: List[str]) -> str:
    """Quote the path + args for the ``Run`` registry value.

    Windows splits the command on spaces but treats double-quoted segments as
    a single argument. ``shlex.join`` uses POSIX rules but the *output*
    happens to also be the conservative-but-correct Windows form for the
    common case (no embedded quotes); when the path contains a space,
    ``shlex.quote`` wraps it in single quotes which Windows would not
    interpret, so we hand-roll double-quoting here.
    """

    def q(s: str) -> str:
        return f'"{s}"' if (" " in s or "\t" in s) else s

    return " ".join(q(p) for p in [exec_path, *args])


class _WindowsAutostartManager(AutostartManager):
    def is_enabled(self) -> AutostartStatus:
        if winreg is None:
            return AutostartStatus(enabled=False)
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                value, _ = winreg.QueryValueEx(key, VALUE_NAME)
        except FileNotFoundError:
            return AutostartStatus(enabled=False)
        except OSError:
            return AutostartStatus(enabled=False)

        # Pull just the executable out of the command line for the GUI to
        # surface; shlex with posix=False keeps Windows-style quoting.
        try:
            parts = shlex.split(value, posix=False)
        except ValueError:
            parts = [value]
        exec_path = parts[0].strip('"') if parts else None
        return AutostartStatus(enabled=True, exec_path=exec_path)

    def enable(self, exec_path: str, args: Optional[List[str]] = None) -> None:
        if winreg is None:
            raise OSError("winreg is unavailable on this platform")
        command = _format_command(exec_path, args if args is not None else DEFAULT_ARGS)
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command)

    def disable(self) -> None:
        if winreg is None:
            return
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
            ) as key:
                winreg.DeleteValue(key, VALUE_NAME)
        except FileNotFoundError:
            pass
        except OSError:
            pass


AutostartManager = _WindowsAutostartManager
