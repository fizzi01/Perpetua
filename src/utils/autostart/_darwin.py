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

from ._base import DEFAULT_ARGS, AutostartManager, AutostartStatus


LABEL = "com.federicoizzi.Perpetua"

# LaunchServices launcher. When ``exec_path`` is a ``.app`` bundle we register
# ``open <bundle>`` rather than the bundle's inner Mach-O: launching the inner
# binary directly bypasses LaunchServices and makes launchd render a second,
# bundle-less dock icon (and defeats single-instance dedup). Going through
# ``open`` reuses the one dock icon and folds into any running instance.
_OPEN_PATH = "/usr/bin/open"
# Marker after which ``open`` forwards the remaining tokens to the launched app.
_OPEN_ARGS_MARKER = "--args"


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
            argv = data.get("ProgramArguments") or []
            exec_path, args = self._parse_program_arguments(argv, data)
            return AutostartStatus(enabled=True, exec_path=exec_path, args=args)
        except (plistlib.InvalidFileException, OSError):
            return AutostartStatus(enabled=False)

    @staticmethod
    def _parse_program_arguments(argv, data):
        """Recover ``(exec_path, args)`` from a plist's ProgramArguments.

        Handles both the LaunchServices form
        ``["/usr/bin/open", "<bundle>.app", "--args", *args]`` (report the
        bundle as exec_path so the stale-path warning stays meaningful) and the
        legacy direct form ``["<exec>", *args]``.
        """
        if not argv:
            return data.get("Program"), []
        if argv[0] == _OPEN_PATH:
            exec_path = argv[1] if len(argv) > 1 else None
            try:
                marker = argv.index(_OPEN_ARGS_MARKER)
                args = list(argv[marker + 1 :])
            except ValueError:
                args = []
            return exec_path, args
        return argv[0], list(argv[1:])

    def enable(self, exec_path: str, args: Optional[List[str]] = None) -> None:
        if not os.path.isabs(exec_path):
            raise ValueError(f"exec_path must be absolute, got: {exec_path}")
        launch_args = list(args) if args is not None else list(DEFAULT_ARGS)
        if exec_path.endswith(".app"):
            program_arguments = [
                _OPEN_PATH,
                exec_path,
                _OPEN_ARGS_MARKER,
                *launch_args,
            ]
        else:
            # Legacy / non-bundle path (e.g. a plain binary): launch directly.
            program_arguments = [exec_path, *launch_args]
        plist = {
            "Label": LABEL,
            "ProgramArguments": program_arguments,
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
        # ``bootstrap`` is a no-op (and leaves the *old* command loaded) if a
        # service with this label is already registered — which is exactly the
        # case when the user switches launch mode. Bootout first so the new
        # ProgramArguments actually take effect without waiting for next login.
        self._launchctl_bootout(path)
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
