"""
macOS-specific permission checking implementation.

This module provides permission checking for macOS systems, including:
- Accessibility permissions
- Input monitoring permissions (keyboard and mouse)
"""


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

import platform
import time
from packaging import version
from typing import Optional

from . import _base
from ._base import PermissionType, PermissionStatus, PermissionResult

# macOS IOKit constants for input monitoring
kIOHIDAccessTypeDenied = 1
kIOHIDAccessTypeGranted = 0
kIOHIDAccessTypeUnknown = 2
kIOHIDRequestTypeListenEvent = 1
kIOHIDRequestTypePostEvent = 0


class PermissionChecker(_base.PermissionChecker):
    """
    macOS-specific implementation of PermissionChecker.

    Handles checking and requesting permissions for:
    - Accessibility (required for keyboard/mouse control)
    - Input Monitoring (required on macOS 10.15+)
    """

    def __init__(self, logger=None):
        """Initialize the macOS permission checker"""
        super().__init__(logger)
        self._iokit_bundle = None
        self._mac_version = version.parse(platform.mac_ver()[0])

    def _load_iokit_bundle(self) -> Optional[dict]:
        """
        Load IOKit bundle for input monitoring checks.

        Returns:
            Dictionary with IOKit functions or None if loading fails
        """
        if self._iokit_bundle is not None:
            return self._iokit_bundle

        try:
            import objc
            from Foundation import NSBundle  # ty:ignore[unresolved-import]

            IOKit = NSBundle.bundleWithIdentifier_("com.apple.framework.IOKit")

            ioset = {}
            functions = [
                ("IOHIDRequestAccess", b"BI"),
                ("IOHIDCheckAccess", b"II"),
            ]

            objc.loadBundleFunctions(IOKit, ioset, functions)  # ty:ignore[unresolved-attribute]
            self._iokit_bundle = ioset
            self._log("IOKit bundle loaded successfully")
            return ioset

        except Exception as e:
            self._log(f"Failed to load IOKit bundle: {e}", level="error")
            return None

    def _is_input_monitoring_required(self) -> bool:
        """
        Check if input monitoring permission is required.

        Input monitoring is required on macOS 10.15 (Catalina) and later.

        Returns:
            True if input monitoring is required, False otherwise
        """
        return self._mac_version >= version.parse("10.15")

    def _check_accessibility_permission(self) -> PermissionResult:
        """
        Check if accessibility permission is granted.

        Returns:
            PermissionResult for accessibility permission
        """
        try:
            import HIServices

            is_trusted = HIServices.AXIsProcessTrusted()  # ty:ignore[unresolved-attribute]
            self._log(
                f"Accessibility permission status: {'granted' if is_trusted else 'denied'}"
            )

            status = PermissionStatus.GRANTED if is_trusted else PermissionStatus.DENIED

            return PermissionResult(
                permission_type=PermissionType.ACCESSIBILITY,
                status=status,
                message="Accessibility permission allows controlling keyboard and mouse",
                can_request=not is_trusted,
            )

        except Exception as e:
            self._log(f"Error checking accessibility permission: {e}", level="error")
            return PermissionResult(
                permission_type=PermissionType.ACCESSIBILITY,
                status=PermissionStatus.UNKNOWN,
                message=f"Failed to check permission: {e}",
                can_request=False,
            )

    def _check_input_monitoring_permission(self) -> PermissionResult:
        """
        Check if input monitoring permission is granted.

        Returns:
            PermissionResult for input monitoring permission
        """
        # Input monitoring only required on macOS 10.15+
        if not self._is_input_monitoring_required():
            return PermissionResult(
                permission_type=PermissionType.KEYBOARD_INPUT,
                status=PermissionStatus.NOT_REQUIRED,
                message="Input monitoring not required on this macOS version",
            )

        iokit = self._load_iokit_bundle()
        if iokit is None:
            return PermissionResult(
                permission_type=PermissionType.KEYBOARD_INPUT,
                status=PermissionStatus.UNKNOWN,
                message="Failed to load IOKit bundle",
                can_request=False,
            )

        try:
            status = iokit["IOHIDCheckAccess"](kIOHIDRequestTypeListenEvent)
            self._log(f"Input monitoring permission status code: {status}")

            if status == kIOHIDAccessTypeGranted:
                perm_status = PermissionStatus.GRANTED
                message = "Input monitoring permission is granted"
                can_request = False
            elif status == kIOHIDAccessTypeDenied:
                perm_status = PermissionStatus.DENIED
                message = "Input monitoring permission is denied"
                can_request = True
            else:
                perm_status = PermissionStatus.UNKNOWN
                message = "Input monitoring permission status is unknown"
                can_request = True

            return PermissionResult(
                permission_type=PermissionType.KEYBOARD_INPUT,
                status=perm_status,
                message=message,
                can_request=can_request,
            )

        except Exception as e:
            self._log(f"Error checking input monitoring permission: {e}", level="error")
            return PermissionResult(
                permission_type=PermissionType.KEYBOARD_INPUT,
                status=PermissionStatus.UNKNOWN,
                message=f"Failed to check permission: {e}",
                can_request=False,
            )

    def check_permission(self, permission_type: PermissionType) -> PermissionResult:
        """
        Check if a specific permission is granted.

        Args:
            permission_type: The type of permission to check

        Returns:
            PermissionResult with the status of the permission
        """
        if permission_type == PermissionType.ACCESSIBILITY:
            return self._check_accessibility_permission()
        elif permission_type in (
            PermissionType.KEYBOARD_INPUT,
            PermissionType.MOUSE_INPUT,
        ):
            return self._check_input_monitoring_permission()
        else:
            return PermissionResult(
                permission_type=permission_type,
                status=PermissionStatus.NOT_REQUIRED,
                message=f"{permission_type.value} permission not implemented for macOS",
            )

    def request_permission(self, permission_type: PermissionType) -> PermissionResult:
        """
        Request a specific permission from the user.

        Args:
            permission_type: The type of permission to request

        Returns:
            PermissionResult with the updated status
        """
        if permission_type == PermissionType.ACCESSIBILITY:
            return self._request_accessibility_permission()
        elif permission_type in (
            PermissionType.KEYBOARD_INPUT,
            PermissionType.MOUSE_INPUT,
        ):
            return self._request_input_monitoring_permission()
        else:
            return PermissionResult(
                permission_type=permission_type,
                status=PermissionStatus.NOT_REQUIRED,
                message=f"{permission_type.value} permission not implemented for macOS",
            )

    def _request_accessibility_permission(self) -> PermissionResult:
        """
        Request accessibility permission with system prompt.

        Returns:
            PermissionResult with updated status
        """
        try:
            import HIServices
            from ApplicationServices import kAXTrustedCheckOptionPrompt  # ty:ignore[unresolved-import]

            self._log("Requesting accessibility permission")

            HIServices.AXIsProcessTrustedWithOptions(  # ty:ignore[unresolved-attribute]
                {kAXTrustedCheckOptionPrompt: True}
            )

            # Give the system a moment to process
            time.sleep(1)

            # Check the result
            return self._check_accessibility_permission()

        except Exception as e:
            self._log(f"Error requesting accessibility permission: {e}", level="error")
            return PermissionResult(
                permission_type=PermissionType.ACCESSIBILITY,
                status=PermissionStatus.UNKNOWN,
                message=f"Failed to request permission: {e}",
                can_request=False,
            )

    def _request_input_monitoring_permission(self) -> PermissionResult:
        """
        Request input monitoring permission.

        Returns:
            PermissionResult with updated status
        """
        if not self._is_input_monitoring_required():
            return PermissionResult(
                permission_type=PermissionType.KEYBOARD_INPUT,
                status=PermissionStatus.NOT_REQUIRED,
                message="Input monitoring not required on this macOS version",
            )

        iokit = self._load_iokit_bundle()
        if iokit is None:
            return PermissionResult(
                permission_type=PermissionType.KEYBOARD_INPUT,
                status=PermissionStatus.UNKNOWN,
                message="Failed to load IOKit bundle",
                can_request=False,
            )

        try:
            self._log("Requesting input monitoring permission")

            result = iokit["IOHIDRequestAccess"](kIOHIDRequestTypeListenEvent)
            self._log(f"IOHIDRequestAccess result: {result}")

            # Open system preferences
            self._open_input_monitoring_settings()

            # Give the system a moment to process
            time.sleep(1)

            # Check the result
            return self._check_input_monitoring_permission()

        except Exception as e:
            self._log(
                f"Error requesting input monitoring permission: {e}", level="error"
            )
            return PermissionResult(
                permission_type=PermissionType.KEYBOARD_INPUT,
                status=PermissionStatus.UNKNOWN,
                message=f"Failed to request permission: {e}",
                can_request=False,
            )

    def check_all_permissions(self) -> dict[PermissionType, PermissionResult]:
        """
        Check all required permissions for the application.

        Returns:
            Dictionary mapping permission types to their results
        """
        self._log("Checking all macOS permissions")

        results = {
            PermissionType.ACCESSIBILITY: self._check_accessibility_permission(),
            PermissionType.KEYBOARD_INPUT: self._check_input_monitoring_permission(),
        }

        return results

    def _open_accessibility_settings(self):
        """Open macOS System Preferences to Accessibility settings"""
        try:
            from AppKit import NSWorkspace, NSURL  # ty:ignore[unresolved-import]

            url = "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
            url_obj = NSURL.alloc().initWithString_(url)
            NSWorkspace.sharedWorkspace().openURL_(url_obj)

            self._log("Opened accessibility settings")

        except Exception as e:
            self._log(f"Failed to open accessibility settings: {e}", level="error")

    def _open_input_monitoring_settings(self):
        """Open macOS System Preferences to Input Monitoring settings"""
        try:
            from AppKit import NSWorkspace, NSURL  # ty:ignore[unresolved-import]

            url = "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent"
            url_obj = NSURL.alloc().initWithString_(url)
            NSWorkspace.sharedWorkspace().openURL_(url_obj)

            self._log("Opened input monitoring settings")

        except Exception as e:
            self._log(f"Failed to open input monitoring settings: {e}", level="error")

    def open_settings(self, permission_type: Optional[PermissionType] = None):
        """
        Open system settings for permission management.

        Args:
            permission_type: Optional specific permission to open settings for
        """
        if permission_type == PermissionType.ACCESSIBILITY:
            self._open_accessibility_settings()
        elif permission_type in (
            PermissionType.KEYBOARD_INPUT,
            PermissionType.MOUSE_INPUT,
        ):
            self._open_input_monitoring_settings()
        else:
            # Default to accessibility settings
            self._open_accessibility_settings()

    def _log(self, message: str, level: str = "info"):
        super()._log(message, level)

    def has_required_permissions(self) -> bool:
        return super().has_required_permissions()

    def get_missing_permissions(self) -> list[PermissionResult]:
        return super().get_missing_permissions()


# Legacy function for backwards compatibility
def check_osx_permissions() -> bool:
    """
    Legacy function for checking macOS permissions.

    This function is maintained for backwards compatibility.
    New code should use DarwinPermissionChecker class instead.

    Returns:
        True if all required permissions are granted, False otherwise
    """
    checker = PermissionChecker()
    return checker.has_required_permissions()
