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

import hashlib
import sys
import importlib
import time
import random
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


class ExponentialBackoff:
    """
    Implements exponential backoff with jitter for connection retry logic.

    Example:
        backoff = ExponentialBackoff(initial_delay=1, max_delay=300, multiplier=2)
        delay = backoff.get_next_delay()
        await asyncio.sleep(delay)
        # On successful connection:
        backoff.reset()
    """

    def __init__(
        self,
        initial_delay: float = 1.0,
        max_delay: float = 300.0,
        multiplier: float = 2.0,
        jitter: bool = True,
        jitter_ratio: float = 0.1,
    ):
        """
        Initialize exponential backoff strategy.

        Args:
            initial_delay: Initial delay in seconds (default: 1.0)
            max_delay: Maximum delay cap in seconds (default: 300.0)
            multiplier: Exponential growth factor (default: 2.0)
            jitter: Whether to apply random jitter (default: True)
            jitter_ratio: Maximum jitter as ratio of delay (default: 0.1 = ±10%)
        """
        self.initial_delay = initial_delay
        self.max_delay = max_delay
        self.multiplier = multiplier
        self.jitter = jitter
        self.jitter_ratio = jitter_ratio

        self._current_delay = initial_delay
        self._attempt = 0

    def get_next_delay(self) -> float:
        """
        Calculate and return the next backoff delay.

        Returns:
            Delay in seconds for the next retry attempt
        """
        self._attempt += 1

        # Calculate exponential delay
        delay = min(self._current_delay, self.max_delay)

        # Apply jitter if enabled
        if self.jitter and delay > 0:
            jitter_range = delay * self.jitter_ratio
            jitter_offset = random.uniform(-jitter_range, jitter_range)
            delay = max(0, delay + jitter_offset)

        # Update current delay for next iteration
        self._current_delay = min(self._current_delay * self.multiplier, self.max_delay)

        return delay

    def reset(self):
        """Reset backoff to initial state after successful connection."""
        self._current_delay = self.initial_delay
        self._attempt = 0

    @property
    def attempt_count(self) -> int:
        """Get current attempt count."""
        return self._attempt

    @property
    def current_delay(self) -> float:
        """Get current delay value (before jitter)."""
        return min(self._current_delay, self.max_delay)
