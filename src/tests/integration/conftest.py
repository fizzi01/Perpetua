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
"""Integration-test fixtures.

These tests wire a real server-side and client-side component graph
together over an in-process *message bridge* (see :mod:`harness`) instead
of sockets/TLS. Only the OS-touching pieces (input capture/injection,
screen enumeration, clipboard) are mocked; the event bus, command
handler, stream framing (msgpack via ``MessageExchange``) and routing
logic are the real production code.

This conftest is deliberately self-contained: it re-imports
``_MOCK_PYNPUT`` from ``tests.unit`` and re-declares the autouse patches
the unit suite relies on, so it has zero blast radius on
``tests/unit/conftest.py``.
"""

from tests.unit import _MOCK_PYNPUT

from unittest.mock import patch

import pytest

_MOCK_PYNPUT()


# ============================================================================
# Asyncio backend matrix (mirror of tests/unit/conftest.py)
# ============================================================================


@pytest.fixture(
    params=[
        pytest.param(("asyncio", {"use_uvloop": True}), id="asyncio+uvloop"),
        pytest.param(("asyncio", {"use_uvloop": False}), id="asyncio"),
    ]
)
def anyio_backend(request):
    return request.param


# ============================================================================
# Autouse safety patches (mirror of tests/unit/conftest.py)
# ============================================================================


@pytest.fixture(autouse=True)
def disable_command_handler_clear():
    """Preserve the class-level CommandHandler registry across tests."""
    with patch("daemon.CommandHandler.clear"):
        yield


@pytest.fixture(autouse=True)
def disable_uinput():
    """Disable pynput's uinput to prevent crashes on Linux during tests."""
    with patch("pynput.keyboard._uinput.Layout"):
        yield


@pytest.fixture(autouse=True)
def disable_delayed_exit():
    """Disable Daemon.delayed_exit to prevent test process from exiting."""
    with patch("daemon.Daemon.delayed_exit"):
        yield


@pytest.fixture(autouse=True)
def disable_permission_watchdog():
    """Disable permission watchdog to prevent interference during tests."""

    async def _noop(self, interval=5.0):
        pass

    with patch("daemon.Daemon._permission_watchdog", _noop):
        yield
