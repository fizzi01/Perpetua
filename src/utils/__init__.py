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

import asyncio
import hashlib
import sys
import importlib
import time
import random
from typing import Any, Coroutine, NamedTuple, Optional, Set

from utils.logging import get_logger


class BackendFailure(NamedTuple):
    """One platform/backend module that could not be imported."""

    package: str
    module: str
    error: str


# How a backend import fails: ImportError (module absent, or pynput aborting
# on dumpkeys), OSError (ctypes dlopen of libei by soname), RuntimeError (one
# of our placeholders touched at import time). Anything else is a bug, not a
# packaging defect, and must stay loud.
BACKEND_IMPORT_ERRORS = (ImportError, OSError, RuntimeError)

# Drained by the daemon once a GUI connects: a backend that cannot be imported
# is almost always a packaging defect and must not degrade silently.
BACKEND_FAILURES: list[BackendFailure] = []


def backend_failures() -> list[BackendFailure]:
    """Every backend import failure recorded so far."""
    return list(BACKEND_FAILURES)


def record_backend_failure(package: str, module: str, error: str) -> BackendFailure:
    """Record and log a backend import failure."""
    failure = BackendFailure(package, module, error)
    BACKEND_FAILURES.append(failure)
    get_logger("utils.backend").error(
        "Backend import failed", package=package, module=module, error=error
    )
    return failure


class _UnavailableMeta(type):
    """Raises on any use of a placeholder, including attribute access."""

    def __getattr__(cls, item: str) -> Any:
        raise RuntimeError(cls._reason)

    def __call__(cls, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError(cls._reason)


def unavailable_symbol(name: str, reason: str) -> type:
    """Build a placeholder that raises ``reason`` the first time it is used."""
    return _UnavailableMeta(
        f"Unavailable{name}", (), {"_reason": reason, "__doc__": reason}
    )


class _UnavailableModule:
    """Stand-in for a platform module that failed to import.

    The daemon owns the IPC socket: if it dies here the GUI has nothing to
    talk to and no way to say why. Attributes hand back placeholders that
    raise when the feature is used.
    """

    def __init__(self, package: str, reason: str) -> None:
        self._package = package
        self._reason = reason

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        return unavailable_symbol(name, f"{self._package}.{name}: {self._reason}")


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
    except BACKEND_IMPORT_ERRORS as e:
        # Fallback on _base if specific module not found
        try:
            module = importlib.import_module("._base", package=package)
            return module
        except BACKEND_IMPORT_ERRORS:
            # Both the platform module and ``_base`` are gone: ``_base`` often
            # re-imports the same backend, so it is no fallback for a missing
            # system library. Degrade instead of raising - see
            # ``_UnavailableModule``.
            reason = f"unable to load module for {platform} ({type(e).__name__}: {e})"
            record_backend_failure(package, module_name, reason)
            return _UnavailableModule(package, reason)


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


_bg_logger = get_logger("BackgroundTasks")


class BackgroundTasks:
    """
    Holds strong refs to fire-and-forget asyncio.Tasks.

    Failed tasks (non-cancellation exceptions) are logged at WARNING via the
    discard callback so silent failures are visible.
    """

    __slots__ = ("_tasks",)

    def __init__(self) -> None:
        self._tasks: Set[asyncio.Task] = set()

    def spawn(self, coro: Coroutine, name: Optional[str] = None) -> asyncio.Task:
        task = (
            asyncio.create_task(coro, name=name) if name else asyncio.create_task(coro)
        )
        self._tasks.add(task)
        task.add_done_callback(self._on_task_done)
        return task

    def _on_task_done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        try:
            exc = task.exception()
        except asyncio.CancelledError:
            return
        if exc is not None:
            task_name = task.get_name() if hasattr(task, "get_name") else "<task>"
            _bg_logger.warning(
                f"Background task {task_name!r} failed: {type(exc).__name__}: {exc}"
            )

    def __len__(self) -> int:
        return len(self._tasks)

    async def drain(self, cancel: bool = False) -> None:
        if not self._tasks:
            return
        # Exclude the current task: a task awaiting itself would deadlock.
        current = asyncio.current_task()
        to_wait = [t for t in self._tasks if t is not current]
        if cancel:
            for t in to_wait:
                t.cancel()
        if to_wait:
            await asyncio.gather(*to_wait, return_exceptions=True)
        self._tasks.difference_update(to_wait)
