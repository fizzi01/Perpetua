"""
Base class for permission checking across different operating systems.

This module provides a common interface for checking system permissions
required by the application (keyboard input monitoring, accessibility, etc.).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class PermissionType(Enum):
    """Types of permissions that can be checked"""

    KEYBOARD_INPUT = "keyboard_input"
    MOUSE_INPUT = "mouse_input"
    ACCESSIBILITY = "accessibility"
    SCREEN_RECORDING = "screen_recording"
    CLIPBOARD = "clipboard"
    NONE = "none"


class PermissionStatus(Enum):
    """Status of a permission check"""

    GRANTED = "granted"
    DENIED = "denied"
    UNKNOWN = "unknown"
    NOT_REQUIRED = "not_required"


@dataclass
class PermissionResult:
    """Result of a permission check"""

    permission_type: PermissionType
    status: PermissionStatus
    message: Optional[str] = None
    can_request: bool = False

    @property
    def is_granted(self) -> bool:
        """Check if permission is granted"""
        return self.status == PermissionStatus.GRANTED

    @property
    def is_denied(self) -> bool:
        """Check if permission is denied"""
        return self.status == PermissionStatus.DENIED

    @property
    def needs_action(self) -> bool:
        """Check if user action is needed"""
        return self.status == PermissionStatus.DENIED and self.can_request


class PermissionChecker(ABC):
    """
    Abstract base class for permission checking.

    Platform-specific implementations should inherit from this class
    and implement the required methods.
    """

    def __init__(self, logger=None):
        """
        Initialize the permission checker.

        Args:
            logger: Optional logger instance for logging permission checks
        """
        self.logger = logger

    def _log(self, message: str, level: str = "info"):
        """Log a message if logger is available"""
        if self.logger:
            log_method = getattr(self.logger, level, None)
            if log_method:
                log_method(message)
        else:
            print(f"[{level.upper()}] {message}")

    @abstractmethod
    def check_permission(self, permission_type: PermissionType) -> PermissionResult:
        """
        Check if a specific permission is granted.

        Args:
            permission_type: The type of permission to check

        Returns:
            PermissionResult with the status of the permission
        """
        pass

    @abstractmethod
    def request_permission(self, permission_type: PermissionType) -> PermissionResult:
        """
        Request a specific permission from the user.

        Args:
            permission_type: The type of permission to request

        Returns:
            PermissionResult with the updated status
        """
        pass

    @abstractmethod
    def check_all_permissions(self) -> dict[PermissionType, PermissionResult]:
        """
        Check all required permissions for the application.

        Returns:
            Dictionary mapping permission types to their results
        """
        pass

    def has_required_permissions(self) -> bool:
        """
        Check if all required permissions are granted.

        Returns:
            True if all required permissions are granted, False otherwise
        """
        results = self.check_all_permissions()
        return all(
            result.is_granted or result.status == PermissionStatus.NOT_REQUIRED
            for result in results.values()
        )

    def get_missing_permissions(self) -> list[PermissionResult]:
        """
        Get list of permissions that are not granted.

        Returns:
            List of PermissionResult for missing permissions
        """
        results = self.check_all_permissions()
        return [
            result
            for result in results.values()
            if result.is_denied or result.status == PermissionStatus.UNKNOWN
        ]

    @abstractmethod
    def open_settings(self, permission_type: Optional[PermissionType] = None):
        """
        Open system settings for permission management.

        Args:
            permission_type: Optional specific permission to open settings for
        """
        pass
