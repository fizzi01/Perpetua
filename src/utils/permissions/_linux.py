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

import grp
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import List, Optional, Tuple

from . import _base
from ._base import PermissionType, PermissionStatus, PermissionResult

IS_LINUX = sys.platform.startswith("linux")

UINPUT_RULE_PATH = "/etc/udev/rules.d/01-perpetua-keyboard.rules"
TTY_RULE_PATH = "/etc/udev/rules.d/12-input.rules"

_SETUP_SCRIPT_NAME = "enable_uinput.sh"

# pkexec reports a dismissed dialog and a failed authentication through the
# exit status only.
_PKEXEC_EXIT_MESSAGES = {
    126: "The authorisation dialog was dismissed.",
    127: "Authentication failed or no polkit agent is running.",
}

MANUAL_INSTRUCTIONS = (
    "Install the rules manually, then restart Perpetua:\n"
    "  sudo sh enable_uinput.sh\n"
    "(the script ships next to the Perpetua executable)"
)

# One dialog at a time: a double click on the gate button would stack two.
_install_lock = threading.Lock()


def _is_executable_owner() -> bool:
    """Return True if the current user owns the running application source file.

    When the process is launched via sudo, SUDO_UID is used to recover the
    original user's UID so that ownership is checked against the real user
    rather than root.
    """
    try:
        # Under sudo, os.getuid() returns 0; SUDO_UID carries the real user.
        uid = int(os.environ.get("SUDO_UID", os.getuid()))
        file_uid = os.stat(__file__).st_uid
        return file_uid == uid
    except (OSError, ValueError):
        return False


def _is_root() -> bool:
    """Return True if the current user is root."""
    return os.geteuid() == 0


def _is_in_input_group() -> bool:
    """Return True if the current user belongs to the 'input' or 'plugdev' group."""
    user_groups = os.getgroups()
    for group_name in ("input", "plugdev"):
        try:
            if grp.getgrnam(group_name).gr_gid in user_groups:
                return True
        except KeyError:
            continue
    return False


def _has_dumpkeys() -> bool:
    # AppRun puts the bundle on PATH, so the bundled dumpkeys resolves here.
    return shutil.which("dumpkeys") is not None


def _can_write_uinput() -> bool:
    return os.access("/dev/uinput", os.W_OK)


def _can_read_console() -> bool:
    # dumpkeys reads the keymap from /dev/tty0 (or /dev/console), which is
    # what the tty rule unlocks.
    return any(os.access(node, os.R_OK) for node in ("/dev/tty0", "/dev/console"))


def _tty_rule_installed() -> bool:
    try:
        content = Path(TTY_RULE_PATH).read_text()
    except OSError:
        return False
    return 'SUBSYSTEM=="tty"' in content and 'TAG+="uaccess"' in content


def _has_input_access() -> bool:
    """Report whether keystrokes can actually be injected.

    Checks real access rather than the presence of the rule files: a user can
    be in the ``input`` group under a distro-provided rule, and the re-check
    after :func:`install_udev_rules` has to tell the truth.
    """
    if _is_root():
        return True
    if not _has_dumpkeys():
        return False
    if not _can_write_uinput():
        return False
    # The uaccess ACL only reaches the active session; on a session that has
    # not been restarted the rule file is the only evidence available.
    return _can_read_console() or _tty_rule_installed()


def _has_display() -> bool:
    """Return True if a display server (X11 or Wayland) is available."""
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _resolve_setup_script() -> Optional[Path]:
    """Locate ``enable_uinput.sh``.

    Packaged builds ship it next to the daemon executable (the AppImage
    bundle, ``/opt`` for deb/rpm); a source checkout still has it under
    ``scripts/``.
    """
    candidates: List[Path] = []
    try:
        candidates.append(Path(sys.executable).resolve().parent / _SETUP_SCRIPT_NAME)
    except OSError:
        pass

    appdir = os.environ.get("APPDIR")
    if appdir:
        candidates.append(
            Path(appdir) / "usr" / "lib" / "Perpetua" / _SETUP_SCRIPT_NAME
        )

    candidates.append(
        Path(__file__).resolve().parents[3] / "scripts" / _SETUP_SCRIPT_NAME
    )

    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _can_prompt_graphically() -> bool:
    return bool(shutil.which("pkexec")) and _has_display()


def escalation_available() -> bool:
    """Return True if the rules can be installed without leaving the GUI.

    The sudo fallback does not count: it needs a terminal the GUI has not got.
    """
    return _is_root() or _can_prompt_graphically()


def install_udev_rules(
    interactive: bool = False, timeout: float = 300.0
) -> Tuple[bool, str]:
    """Run the privileged setup script once.

    The script elevates itself and copies itself out of the AppImage's FUSE
    mount, so there is nothing to stage here.

    Args:
        interactive: Leave stdin attached so the script may fall back to
            sudo. Only the CLI path passes True - a sudo prompt on the
            terminal that started the daemon is invisible to the GUI user.
        timeout: Seconds to wait for the authorisation dialog.

    Returns:
        ``(ok, detail)``; *detail* is shown to the user verbatim.
    """
    if not _install_lock.acquire(blocking=False):
        return False, "A permission install is already in progress."
    try:
        script = _resolve_setup_script()
        if script is None:
            return False, f"{_SETUP_SCRIPT_NAME} not found.\n{MANUAL_INSTRUCTIONS}"

        if not interactive and not escalation_available():
            return False, (
                "No way to ask for administrator rights (no pkexec in a "
                f"desktop session).\n{MANUAL_INSTRUCTIONS}"
            )

        proc = subprocess.run(
            ["/bin/sh", str(script), "configure"],
            stdin=None if interactive else subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if proc.returncode == 0:
            return True, (proc.stdout or "").strip() or "udev rules installed."

        detail = _PKEXEC_EXIT_MESSAGES.get(proc.returncode)
        if detail is None:
            detail = (proc.stderr or proc.stdout or "").strip() or (
                f"the helper exited with status {proc.returncode}"
            )
        return False, f"{detail}\n{MANUAL_INSTRUCTIONS}"
    except subprocess.TimeoutExpired:
        return False, f"Timed out waiting for authorisation.\n{MANUAL_INSTRUCTIONS}"
    except OSError as e:
        return False, f"Could not run the helper: {e}\n{MANUAL_INSTRUCTIONS}"
    finally:
        _install_lock.release()


class PermissionChecker(_base.PermissionChecker):
    def check_permission(self, permission_type: PermissionType) -> PermissionResult:
        match permission_type:
            case PermissionType.KEYBOARD_INPUT:
                if not _has_input_access():
                    can_request = escalation_available()
                    return PermissionResult(
                        permission_type=permission_type,
                        status=PermissionStatus.DENIED,
                        message=(
                            "Perpetua needs one-time access to /dev/uinput; "
                            "installing the udev rules requires administrator "
                            "rights."
                            if can_request
                            else "No write access to /dev/uinput.\n"
                            f"{MANUAL_INSTRUCTIONS}"
                        ),
                        can_request=can_request,
                    )
                return PermissionResult(
                    permission_type=permission_type,
                    status=PermissionStatus.GRANTED,
                )
            case PermissionType.MOUSE_INPUT:
                if not _has_display():
                    return PermissionResult(
                        permission_type=permission_type,
                        status=PermissionStatus.DENIED,
                        message="neither DISPLAY nor WAYLAND_DISPLAY is set",
                        can_request=False,
                    )
                return PermissionResult(
                    permission_type=permission_type,
                    status=PermissionStatus.GRANTED,
                )

            case PermissionType.NONE:
                return PermissionResult(
                    permission_type=permission_type,
                    status=PermissionStatus.NOT_REQUIRED,
                )

            case _:
                return PermissionResult(
                    permission_type=permission_type,
                    status=PermissionStatus.UNKNOWN,
                )

    def request_permission(self, permission_type: PermissionType) -> PermissionResult:
        """Install the udev rules, then report the live state.

        Linux has no OS-level prompt: the "request" is a privileged helper run
        once. Blocking is fine, the daemon calls this in an executor.
        """
        if permission_type is not PermissionType.KEYBOARD_INPUT:
            return self.check_permission(permission_type)

        current = self.check_permission(permission_type)
        if current.is_granted:
            return current

        ok, detail = install_udev_rules(interactive=False)
        self._log(
            f"udev rule installation: {'ok' if ok else 'failed'} - {detail}",
            "info" if ok else "warning",
        )

        result = self.check_permission(permission_type)
        if result.is_granted:
            return result
        if ok:
            # Rules written but access still denied: the usermod fallback,
            # which only takes effect on the next login.
            detail = (
                "Rules installed, but the new access is not active in this "
                "session yet. Log out and back in, then restart Perpetua."
            )
        return PermissionResult(
            permission_type=permission_type,
            status=PermissionStatus.DENIED,
            message=detail,
            can_request=escalation_available(),
        )

    def check_all_permissions(self) -> dict[PermissionType, PermissionResult]:
        return {
            permission: self.check_permission(permission)
            for permission in [
                PermissionType.KEYBOARD_INPUT,
                PermissionType.MOUSE_INPUT,
            ]
        }

    def open_settings(self, permission_type: Optional[PermissionType] = None):
        pass
