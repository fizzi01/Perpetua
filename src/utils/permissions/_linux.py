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

from typing import Optional

from . import _base
from ._base import PermissionType, PermissionStatus, PermissionResult


class PermissionChecker(_base.PermissionChecker):
    def check_permission(self, permission_type: PermissionType) -> PermissionResult:
        return PermissionResult(
            permission_type=permission_type, status=PermissionStatus.GRANTED
        )

    def request_permission(self, permission_type: PermissionType) -> PermissionResult:
        return self.check_permission(permission_type)

    def check_all_permissions(self) -> dict[PermissionType, PermissionResult]:
        permissions = {}
        for permission in PermissionType:
            permissions[permission] = self.check_permission(permission)
        return permissions

    def open_settings(self, permission_type: Optional[PermissionType] = None):
        pass
