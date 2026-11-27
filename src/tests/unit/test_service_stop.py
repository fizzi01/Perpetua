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
"""A stop that times out must still leave the daemon able to start a service,
and must not leave a thread that holds the process at exit."""

import asyncio
import threading
import types
from unittest.mock import MagicMock

import pytest

from daemon import Daemon
from service.client import Client
from service.server import Server


class _StuckService:
    def __init__(self):
        self.abandoned = False

    async def stop(self):
        await asyncio.sleep(3600)

    def abandon(self):
        self.abandoned = True


@pytest.mark.anyio
async def test_a_stop_that_times_out_marks_the_service_stopped():
    fake = types.SimpleNamespace(SERVICE_STOP_TIMEOUT=0.05, _logger=MagicMock())
    service = _StuckService()

    stopped = await Daemon._stop_service_bounded(fake, "server", service)

    assert stopped is False
    assert service.abandoned, "is_running() stays True and START_CLIENT is refused"


@pytest.mark.parametrize("cls", [Server, Client])
def test_abandon_clears_running(cls):
    service = object.__new__(cls)
    service._running = True
    service._connected = True
    service._is_starting = threading.Event()
    service._is_starting.set()
    service._logger = MagicMock()
    service.cleanup = MagicMock()

    service.abandon()

    assert service.is_running() is False
    service.cleanup.assert_called_once()


@pytest.mark.anyio
async def test_a_stuck_sync_stop_does_not_hold_an_executor_worker():
    gate = threading.Event()
    ran_on = []

    class _Component:
        def stop(self):
            ran_on.append(threading.current_thread())
            gate.wait(5)

    server = object.__new__(Server)
    server._logger = MagicMock()
    server.SYNC_STOP_TIMEOUT = 0.05
    try:
        await server._stop_sync_component("mouse", _Component())

        assert ran_on, "the stop must have run"
        # A default-executor worker would make shutdown_default_executor (and
        # so the daemon's exit) wait for this stop to return.
        assert ran_on[0].daemon is True
        assert not ran_on[0].name.startswith("asyncio_")
    finally:
        gate.set()


@pytest.mark.anyio
async def test_a_sync_stop_error_is_logged():
    class _Component:
        def stop(self):
            raise RuntimeError("boom")

    server = object.__new__(Server)
    server._logger = MagicMock()
    server.SYNC_STOP_TIMEOUT = 1.0

    await server._stop_sync_component("mouse", _Component())

    assert server._logger.error.call_args.kwargs["error"] == "boom"
