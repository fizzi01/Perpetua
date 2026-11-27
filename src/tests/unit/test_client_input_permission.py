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
"""A client that may not inject input stops instead of retrying.

Retrying is another consent dialog on a machine that may have no peripherals
to answer it; reconnecting from the GUI is the retry.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from event import BusEventType
from event.notification import NotificationEventType
from service.client import Client


def _client(running=True):
    client = object.__new__(Client)
    client._running = running
    client._logger = MagicMock()
    client._send_notification = AsyncMock()
    client.stop = AsyncMock()
    client.event_bus = MagicMock()
    return client


@pytest.mark.anyio
async def test_a_denial_notifies_the_gui_and_stops_the_client():
    client = _client()

    await client._on_input_permission_denied("RemoteDesktop permission refused")

    event = client._send_notification.await_args.args[0]
    assert event.event_type == NotificationEventType.SERVICE_ERROR
    assert event.data["reason"] == "input_permission_denied"
    assert "refused" in event.data["error"]
    client.stop.assert_awaited_once()


@pytest.mark.anyio
async def test_a_denial_after_the_client_stopped_does_nothing():
    client = _client(running=False)

    await client._on_input_permission_denied("late")

    client._send_notification.assert_not_awaited()
    client.stop.assert_not_awaited()


@pytest.mark.anyio
async def test_components_start_listening_for_the_denial():
    client = _client()
    for name in (
        "_enable_command_stream",
        "_enable_mouse_stream",
        "_enable_keyboard_stream",
        "_enable_clipboard_stream",
    ):
        setattr(client, name, AsyncMock())

    await client._initialize_components()

    client.event_bus.subscribe.assert_any_call(
        event_type=BusEventType.CLIENT_INPUT_PERMISSION_DENIED,
        callback=client._on_input_permission_denied,
    )
