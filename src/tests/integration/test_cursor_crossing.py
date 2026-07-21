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
"""End-to-end cursor crossing: server edge -> client activation -> return."""

import pytest

from event import (
    BusEventType,
    MouseEvent,
    ClientConnectedEvent,
)
from network.stream import StreamType

from tests.integration.harness import build_bridge


# Server monitor is a single 1920x1080 display at the origin; the client
# lives off its RIGHT edge.
_RIGHT_BINDING = {
    "server_monitor_id": 0,
    "server_edge": "right",
    "server_axis_start": 0.0,
    "server_axis_end": 1.0,
    "server_monitor_min_x": 0,
    "server_monitor_min_y": 0,
    "server_monitor_max_x": 1920,
    "server_monitor_max_y": 1080,
    "client_monitor_id": 0,
    "client_edge": "left",
    "client_axis_start": 0.0,
    "client_axis_end": 1.0,
}


async def _connect_client(h, edge_bindings):
    await h.server_bus.dispatch(
        event_type=BusEventType.CLIENT_CONNECTED,
        data=ClientConnectedEvent(
            client_uid=h.client_uid,
            streams=[StreamType.MOUSE, StreamType.KEYBOARD],
            edge_bindings=edge_bindings,
        ),
    )
    await h.settle()


def _drive_to_right_edge(listener):
    # MOVEMENT_HISTORY_N_THRESHOLD samples with a consistent rightward
    # push, ending pinned on the last on-screen column.
    for x in range(1908, 1920, 2):
        listener.on_move(x, 500)
    listener.on_move(1919, 500)


@pytest.mark.anyio
async def test_server_edge_cross_activates_client():
    """A rightward edge crossing on the server activates the client."""
    h = await build_bridge()
    try:
        await _connect_client(h, [_RIGHT_BINDING])
        server_events = h.track(h.server_bus, BusEventType.ACTIVE_SCREEN_CHANGED)

        _drive_to_right_edge(h.server.listener)
        await h.wait_until(lambda: h.client.mouse._is_active)

        # Client became active off the crossing.
        assert h.client.mouse._is_active is True
        # Server now routes to the client.
        assert h.server.listener._listening is True
        assert h.server.listener._active_client_uid == h.client_uid
        # ACTIVE_SCREEN_CHANGED fired on the server, naming the client.
        active = [
            data
            for et, data in server_events
            if et == BusEventType.ACTIVE_SCREEN_CHANGED
        ]
        assert active, "expected ACTIVE_SCREEN_CHANGED on the server bus"
        assert active[-1].active_screen == h.client_uid
    finally:
        await h.stop()


@pytest.mark.anyio
async def test_server_mouse_events_forwarded_while_active():
    """With the client active, moves/clicks/scrolls reach the client injector."""
    h = await build_bridge()
    try:
        await _connect_client(h, [_RIGHT_BINDING])
        _drive_to_right_edge(h.server.listener)
        await h.wait_until(lambda: h.client.mouse._is_active)
        # Drain the landing POSITION_ACTION the crossing packs onto the
        # mouse stream so it can't race the move asserted below.
        await h.settle(30)

        mock = h.client.mouse_mock
        move_calls_before = mock.move.call_count

        # Absolute move at centre-screen: normalized (0.5, 0.5) denormalizes
        # against the client's 1920x1080 target bbox to (960, 540). Feed it
        # straight through the mouse stream the way the server does.
        await h.server.listener.stream.send(
            MouseEvent(x=0.5, y=0.5, action=MouseEvent.MOVE_ACTION)
        )
        await h.wait_until(lambda: mock.position == (960, 540))
        assert mock.position == (960, 540)
        assert mock.move.call_count == move_calls_before  # absolute, not relative

        # Click forwarded.
        h.server.listener.on_click(200, 300, _Button("left"), True)
        await h.wait_until(lambda: mock.press.called)
        assert mock.press.called

        # Scroll forwarded.
        h.server.listener.on_scroll(0, 0, 1, -1)
        await h.wait_until(lambda: mock.scroll.called)
        mock.scroll.assert_called_with(1, -1)
    finally:
        await h.stop()


@pytest.mark.anyio
async def test_client_return_to_server():
    """Client pushing off its bound edge hands control back to the server."""
    h = await build_bridge()
    try:
        await _connect_client(h, [_RIGHT_BINDING])
        _drive_to_right_edge(h.server.listener)
        await h.wait_until(lambda: h.client.mouse._is_active)
        assert h.server.listener._active_client_uid == h.client_uid

        server_events = h.track(h.server_bus, BusEventType.ACTIVE_SCREEN_CHANGED)

        # The activation pushed the topology (edge bindings + server bbox)
        # to the client, so it can resolve its own return-to-server route.
        ctrl = h.client.mouse
        assert ctrl._edge_bindings, "client should have received the topology"
        assert ctrl._server_bbox == (0, 0, 1920, 1080)

        # Cursor pushed against the client's LEFT edge (bound to server RIGHT).
        for x in range(10, 0, -2):
            ctrl._movement_history.append((x, 500))
        h.client.mouse_mock.position = (0, 500)
        await ctrl._check_edge()
        await h.settle(20)

        # Client relinquished control...
        assert ctrl._is_active is False
        # ...and the server saw the return (ACTIVE_SCREEN_CHANGED -> None).
        returned = [
            data
            for et, data in server_events
            if et == BusEventType.ACTIVE_SCREEN_CHANGED and data.active_screen is None
        ]
        assert returned, "expected a return-to-server ACTIVE_SCREEN_CHANGED(None)"
        assert h.server.listener._listening is False
        assert h.server.listener._active_client_uid is None
    finally:
        await h.stop()


class _Button:
    """Minimal pynput-Button stand-in exposing ``.name`` (e.g. 'left')."""

    def __init__(self, name):
        self.name = name
