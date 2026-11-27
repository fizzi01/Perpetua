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

from event import BusEventType, CommandEvent
from network.stream import StreamType
from event.notification import NotificationEventType
from service.client import Client


def _client(running=True):
    client = object.__new__(Client)
    client._running = running
    client._logger = MagicMock()
    client._send_notification = AsyncMock()
    client.stop = AsyncMock()
    client.event_bus = MagicMock()
    client._stream_handlers = {}
    client._input_consent = None
    client.main_client = None
    client.config = MagicMock(get_uid=lambda: "c1")
    return client


def _command_stream(client, active=True):
    stream = MagicMock(send=AsyncMock(), flush=AsyncMock())
    stream.is_active.return_value = active
    client._stream_handlers[StreamType.COMMAND] = stream
    return stream


def _sent(stream):
    return [c.args[0] for c in stream.send.await_args_list]


@pytest.mark.anyio
async def test_a_denial_notifies_the_gui_and_stops_the_client():
    client = _client()

    await client._on_input_permission_denied("Remote input permission was denied")

    event = client._send_notification.await_args.args[0]
    assert event.event_type == NotificationEventType.SERVICE_ERROR
    assert event.data["reason"] == "input_permission_denied"
    assert event.data["error"] == "Remote input permission was denied"
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
    client.event_bus.subscribe.assert_any_call(
        event_type=BusEventType.CLIENT_INPUT_CONSENT,
        callback=client._on_input_consent,
    )


@pytest.mark.anyio
async def test_a_denial_reaches_the_server_before_the_client_stops():
    client = _client()
    stream = _command_stream(client)
    order = []
    stream.flush.side_effect = lambda: order.append("flush")
    client.stop.side_effect = lambda: order.append("stop")

    await client._on_input_permission_denied("not answered")

    (cmd,) = _sent(stream)
    assert cmd.command == CommandEvent.CLIENT_INPUT_CONSENT
    assert cmd.params == {
        "client_uid": "c1",
        "state": "denied",
        "reason": "not answered",
    }
    assert order == ["flush", "stop"]


@pytest.mark.anyio
@pytest.mark.parametrize("state", ["pending", "granted"])
async def test_consent_reaches_the_gui_and_the_server(state):
    client = _client()
    stream = _command_stream(client)

    await client._on_input_consent(state)

    event = client._send_notification.await_args.args[0]
    assert event.event_type == NotificationEventType.INPUT_CONSENT
    assert event.data["state"] == state
    (cmd,) = _sent(stream)
    assert cmd.params["state"] == state


@pytest.mark.anyio
async def test_a_dialog_before_the_connection_is_reported_once_connected():
    # The input layer starts with the components, before the streams run.
    client = _client()
    stream = _command_stream(client, active=False)
    await client._on_input_consent("pending")
    assert _sent(stream) == []

    stream.is_active.return_value = True
    client._connected = False
    client._initialize_components = AsyncMock()
    client.save_config = AsyncMock()
    client.is_stream_enabled = MagicMock(return_value=False)
    client.event_bus = MagicMock(dispatch=AsyncMock())
    client._connection_info_payload = MagicMock(return_value={})
    await client._on_connected(MagicMock())

    (cmd,) = _sent(stream)
    assert cmd.params["state"] == "pending"


@pytest.mark.anyio
async def test_unknown_states_and_a_stopped_client_are_ignored():
    client = _client()
    stream = _command_stream(client)
    await client._on_input_consent("bogus")
    client._running = False
    await client._on_input_consent("pending")
    assert _sent(stream) == []
    client._send_notification.assert_not_awaited()
