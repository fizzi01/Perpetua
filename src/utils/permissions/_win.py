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
