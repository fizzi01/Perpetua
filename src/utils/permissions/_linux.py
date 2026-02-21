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
from typing import Optional

from . import _base
from ._base import PermissionType, PermissionStatus, PermissionResult


def _is_root() -> bool:
    """Return True if the process is running as root."""
    return os.getuid() == 0


def _is_in_input_group() -> bool:
    """Return True if the current user belongs to the 'input' group."""
    try:
        input_gid = grp.getgrnam("input").gr_gid
        return input_gid in os.getgroups()
    except KeyError:
        return False


def _has_input_access() -> bool:
    """Return True if the process can access /dev/input devices."""
    return _is_root() or _is_in_input_group()


def _has_display() -> bool:
    """Return True if a display server (X11 or Wayland) is available."""
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


class PermissionChecker(_base.PermissionChecker):
    def check_permission(self, permission_type: PermissionType) -> PermissionResult:
        match permission_type:
            case PermissionType.KEYBOARD_INPUT | PermissionType.MOUSE_INPUT | PermissionType.ACCESSIBILITY:
                if _has_input_access():
                    return PermissionResult(
                        permission_type=permission_type,
                        status=PermissionStatus.GRANTED,
                    )
                reason = (
                    "root privileges or membership in the 'input' group are required"
                )
                return PermissionResult(
                    permission_type=permission_type,
                    status=PermissionStatus.DENIED,
                    message=reason,
                    can_request=False,
                )

            case PermissionType.SCREEN_RECORDING | PermissionType.CLIPBOARD:
                if not _has_display():
                    return PermissionResult(
                        permission_type=permission_type,
                        status=PermissionStatus.DENIED,
                        message="neither DISPLAY nor WAYLAND_DISPLAY is set",
                        can_request=False,
                    )
                if not _has_input_access():
                    return PermissionResult(
                        permission_type=permission_type,
                        status=PermissionStatus.DENIED,
                        message="root privileges or membership in the 'input' group are required",
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
        # On Linux permissions cannot be requested interactively; re-check current state.
        return self.check_permission(permission_type)

    def check_all_permissions(self) -> dict[PermissionType, PermissionResult]:
        return {permission: self.check_permission(permission) for permission in PermissionType}

    def open_settings(self, permission_type: Optional[PermissionType] = None):
        pass
