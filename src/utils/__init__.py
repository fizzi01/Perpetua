
#  Perpatua - open-source and cross-platform KVM software.
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

import hashlib
import sys
import importlib
import time
from typing import Optional


def backend_module(package: str, platform_map: Optional[dict] = None):
    """
    Dynamically loads the platform-specific module for the operating system.

    Args:
        package: Package name (e.g. 'input.mouse')
        platform_map: Dictionary mapping sys.platform to module names.
                     If None, uses the _<platform> convention

    Returns:
        The loaded module

    Raises:
        OSError: If the operating system is not supported
        ImportError: If the module cannot be imported
    """
    if platform_map is None:
        platform_map = {
            "darwin": "_darwin",
            "win32": "_win",
            "linux": "_linux",
        }

    platform = sys.platform
    module_name = platform_map.get(platform)

    if module_name is None:
        raise OSError(f"Unsupported operating system: {platform}")

    try:
        module = importlib.import_module(f".{module_name}", package=package)
        return module
    except ImportError as e:
        # Fallback on _base if specific module not found
        try:
            module = importlib.import_module("._base", package=package)
            return module
        except ImportError:
            raise ImportError(f"Unable to load module for {platform} -> {e}")


def export_module_symbols(module, target_globals):
    """
    Exports all public symbols from a module to the target namespace.

    Args:
        module: The module to export from
        target_globals: The globals() dictionary of the target module
    """
    for attr in dir(module):
        if not attr.startswith("_"):
            target_globals[attr] = getattr(module, attr)


class UIDGenerator:
    """
    A simple UID generator utility.
    """

    UID_LEN: int = 48

    @staticmethod
    def generate_uid(key: str, uid_len: int = 0) -> str:
        """
        It generates a unique identifier for the service instance.

        Args:
            key: A string key to base the UID on.
        Returns:
            A unique identifier string.
        """
        id_len = uid_len if uid_len > 0 else UIDGenerator.UID_LEN
        unique_string = f"{key}-{time.time()}"
        try:
            uid = hashlib.sha256(unique_string.encode()).hexdigest()[:id_len]
            return uid
        except Exception as e:
            raise RuntimeError(f"{e}")
