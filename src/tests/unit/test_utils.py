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

import pytest

import utils.net
from utils import BackgroundTasks


def test_get_local_ip():
    ip = utils.net.get_local_ip()
    assert ip is not None, "Local IP address should not be None"
    octets = ip.split(".")
    assert len(octets) == 4, "IP address should have 4 octets"
    # It should not be a loopback address
    assert not ip.startswith("127."), (
        "Local IP address should not be a loopback address"
    )
    # It should not be a 0.0.0.0
    assert ip != "0.0.0.0", "Local IP address should not be 0.0.0.0"
    for octet in octets:
        assert 0 <= int(octet) <= 255, (
            f"Each octet should be between 0 and 255, got {octet}"
        )


def test_get_local_ip_exception():
    import socket

    original_socket = socket.socket

    class FailingSocket:
        def __init__(self, *args, **kwargs):
            pass

        def connect(self, address):
            raise Exception("Simulated failure")

        def getsockname(self):
            return ("", 0)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    socket.socket = FailingSocket
    # Bypass the TTL cache so we actually hit the patched socket.
    utils.net.invalidate_local_ip_cache()
    try:
        try:
            utils.net.get_local_ip()
            assert False, "Expected MissingIpError to be raised"
        except utils.net.MissingIpError as e:
            assert "Could not determine local IP address" in str(e), (
                "Error message should indicate failure to determine IP"
            )
    finally:
        socket.socket = original_socket
        utils.net.invalidate_local_ip_cache()


# ---------------------------------------------------------------------------
# BackgroundTasks
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_background_tasks_spawn_success_no_log(monkeypatch):
    """Successful tasks do not produce WARNING logs from BackgroundTasks."""
    import utils as utils_pkg

    calls = []
    monkeypatch.setattr(
        utils_pkg._bg_logger,
        "warning",
        lambda msg, **kw: calls.append((msg, kw)),
    )

    bg = BackgroundTasks()

    async def ok():
        return 42

    task = bg.spawn(ok())
    await task
    await asyncio.sleep(0)

    assert task.result() == 42
    assert len(bg) == 0, "task should be discarded after completion"
    assert calls == [], f"unexpected log calls: {calls}"


@pytest.mark.anyio
async def test_background_tasks_spawn_exception_logs_warning(monkeypatch):
    """A raising task triggers logger.warning via the discard callback."""
    import utils as utils_pkg

    calls = []
    monkeypatch.setattr(
        utils_pkg._bg_logger,
        "warning",
        lambda msg, **kw: calls.append(msg),
    )

    bg = BackgroundTasks()

    async def boom():
        raise RuntimeError("kaboom")

    task = bg.spawn(boom(), name="explosion")
    with pytest.raises(RuntimeError):
        await task
    await asyncio.sleep(0)

    assert len(bg) == 0
    assert len(calls) == 1, f"expected one warning, got: {calls}"
    msg = calls[0]
    assert "kaboom" in msg, f"missing exception message: {msg!r}"
    assert "explosion" in msg, f"missing task name: {msg!r}"
    assert "RuntimeError" in msg, f"missing exception type: {msg!r}"


@pytest.mark.anyio
async def test_background_tasks_drain_cancel():
    """drain(cancel=True) cancels in-flight tasks without raising."""
    bg = BackgroundTasks()

    async def long_running():
        await asyncio.sleep(10)

    bg.spawn(long_running())
    bg.spawn(long_running())
    assert len(bg) == 2

    await bg.drain(cancel=True)
    assert len(bg) == 0


@pytest.mark.anyio
async def test_background_tasks_cancelled_no_log(monkeypatch):
    """Cancelled tasks should NOT emit a warning (cancellation is expected)."""
    import utils as utils_pkg

    calls = []
    monkeypatch.setattr(
        utils_pkg._bg_logger,
        "warning",
        lambda msg, **kw: calls.append(msg),
    )

    bg = BackgroundTasks()

    async def sleeper():
        await asyncio.sleep(10)

    bg.spawn(sleeper(), name="canceltest")
    await bg.drain(cancel=True)
    await asyncio.sleep(0)

    assert calls == [], f"cancellation should not log: {calls}"
