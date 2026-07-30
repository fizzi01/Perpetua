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
"""Sender-loop behaviour when the transport is gone.

``MissingTransportError`` used to be logged and retried with
``asyncio.sleep(0)`` while leaving ``_active_client`` set - so every queued
event failed the same way, immediately, forever: a hot loop that pegged a core
and buried the log. That is what "Missing transport" spam plus a service that
looked stuck actually was.
"""

from tests.unit import _MOCK_PYNPUT

from unittest.mock import AsyncMock, MagicMock

import pytest

_MOCK_PYNPUT()

from network.data import MissingTransportError  # noqa: E402
from network.stream import StreamType  # noqa: E402
from network.stream.handler import (  # noqa: E402
    _ClientStreamHandler,
    _ServerStreamHandler,
)


@pytest.fixture
def event_bus():
    bus = MagicMock()
    bus.subscribe = MagicMock()
    return bus


@pytest.fixture
def clients():
    manager = MagicMock()
    manager.get_client = MagicMock(return_value=None)
    manager.get_clients = MagicMock(return_value=[])
    return manager


def _build(cls, clients, event_bus):
    handler = cls(
        stream_type=StreamType.MOUSE,
        clients=clients,
        event_bus=event_bus,
        handler_id=f"test-{cls.__name__}",
    )
    handler.msg_exchange = AsyncMock()
    handler._logger = MagicMock()
    return handler


async def _run_until_first_sleep(handler, monkeypatch):
    """Run ``_core_sender`` for exactly one failing iteration.

    Stops the loop from inside the post-error sleep, and returns the delays it
    asked for - a zero delay there is precisely the spin being guarded against.
    """
    import asyncio as _asyncio

    delays: list[float] = []
    real_sleep = _asyncio.sleep

    async def fake_sleep(delay, *args, **kwargs):
        delays.append(delay)
        handler._active = False
        return await real_sleep(0)

    monkeypatch.setattr("network.stream.handler.asyncio.sleep", fake_sleep)
    handler._active = True
    await handler._core_sender()
    return delays


class TestErrorBackoff:
    def test_error_backoff_is_not_zero(self, clients, event_bus):
        # ``_waiting_time`` stays 0 for the happy path, so the error path needs
        # a floor of its own; reusing 0 there is what made a failure a spin.
        handler = _build(_ServerStreamHandler, clients, event_bus)
        assert handler._waiting_time == 0
        assert handler._error_backoff > 0


class TestServerMissingTransport:
    @pytest.mark.anyio
    async def test_stands_down_and_backs_off(self, clients, event_bus, monkeypatch):
        handler = _build(_ServerStreamHandler, clients, event_bus)
        handler._active_client = MagicMock()
        handler._send_clause = lambda: True

        calls = 0

        async def failing_send_logic():
            nonlocal calls
            calls += 1
            raise MissingTransportError("no transport")

        handler._send_logic = failing_send_logic

        delays = await _run_until_first_sleep(handler, monkeypatch)

        assert calls == 1
        assert delays == [handler._error_backoff]
        assert handler._active_client is None, (
            "keeping the active client set is what made every queued event "
            "re-raise immediately"
        )
        assert not handler._send_ready.is_set(), (
            "the loop must suspend on _send_ready, not keep dequeuing"
        )

    def test_logs_at_most_once_per_interval(self, clients, event_bus):
        handler = _build(_ServerStreamHandler, clients, event_bus)

        for _ in range(3):
            handler._log_transport_loss("Missing transport")

        assert handler._logger.warning.call_count == 1

    def test_logs_again_after_the_interval(self, clients, event_bus):
        handler = _build(_ServerStreamHandler, clients, event_bus)

        handler._log_transport_loss("Missing transport")
        # Pretend the interval elapsed rather than sleeping through it.
        handler._last_transport_warning -= handler._TRANSPORT_WARNING_INTERVAL + 1
        handler._log_transport_loss("Missing transport")

        assert handler._logger.warning.call_count == 2


class TestClientMissingTransport:
    @pytest.mark.anyio
    async def test_backs_off_and_logs_once(self, clients, event_bus, monkeypatch):
        # The client side has no ``_active_client``; its own reconnect logic
        # owns recovery, so this only has to stop spinning.
        handler = _build(_ClientStreamHandler, clients, event_bus)
        handler._send_clause = lambda: False

        calls = 0

        async def failing_send_logic():
            nonlocal calls
            calls += 1
            raise MissingTransportError("no transport")

        handler._send_logic = failing_send_logic

        delays = await _run_until_first_sleep(handler, monkeypatch)

        assert calls == 1
        assert delays == [handler._error_backoff]
        assert handler._logger.warning.call_count == 1
