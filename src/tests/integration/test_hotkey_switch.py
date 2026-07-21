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
"""Hotkey-driven focus switching across the server<->client bridge.

The keyboard listener turns hotkeys into ``SCREEN_SWITCH_*`` bus requests;
these tests dispatch those requests directly on the server bus (the exact
thing ``ServerKeyboardListener`` emits) and assert the real
``ServerMouseListener`` resolves them and drives the crossing over the
command bridge.
"""

import pytest

from event import (
    BusEventType,
    ClientLayoutUpdatedEvent,
    ForceScreenChangeCommandEvent,
    ScreenSwitchCycleRequestEvent,
    ScreenSwitchDirectionalRequestEvent,
)
from input.utils import ScreenEdge

from tests.integration.harness import build_bridge
from tests.integration.test_topology import _1080P, _bindings_for, _server_monitors


def _edge_placement(server_edge):
    """A client placement abutting one server edge of the 1080p monitor."""
    return {
        "left": {
            "client_monitor_id": 0,
            "workspace_x": -1920,
            "workspace_y": 0,
            "width": 1920,
            "height": 1080,
        },
        "right": {
            "client_monitor_id": 0,
            "workspace_x": 1920,
            "workspace_y": 0,
            "width": 1920,
            "height": 1080,
        },
    }[server_edge]


async def _connect(h, uid, edge_bindings):
    from event import ClientConnectedEvent
    from network.stream import StreamType

    await h.server_bus.dispatch(
        event_type=BusEventType.CLIENT_CONNECTED,
        data=ClientConnectedEvent(
            client_uid=uid,
            streams=[StreamType.MOUSE, StreamType.KEYBOARD],
            edge_bindings=edge_bindings,
        ),
    )
    await h.settle()


@pytest.mark.anyio
async def test_directional_hotkey_activates_neighbour():
    """Ctrl+Shift+P+arrow resolves the client on that edge and activates it."""
    h = await build_bridge()
    try:
        server = _server_monitors([_1080P])
        await _connect(h, h.client_uid, _bindings_for(_edge_placement("right"), server))
        listener = h.server.listener

        # No client above the server -> a TOP directional resolves nothing.
        await h.server_bus.dispatch(
            event_type=BusEventType.SCREEN_SWITCH_DIRECTIONAL_REQUEST,
            data=ScreenSwitchDirectionalRequestEvent(edge=ScreenEdge.TOP),
        )
        await h.settle(10)
        assert listener._active_client_uid is None
        assert h.client.mouse._is_active is False

        # RIGHT directional resolves the client bound off the right edge.
        await h.server_bus.dispatch(
            event_type=BusEventType.SCREEN_SWITCH_DIRECTIONAL_REQUEST,
            data=ScreenSwitchDirectionalRequestEvent(edge=ScreenEdge.RIGHT),
        )
        await h.wait_until(lambda: h.client.mouse._is_active)

        assert listener._active_client_uid == h.client_uid
        assert h.client.mouse._is_active is True
    finally:
        await h.stop()


@pytest.mark.anyio
async def test_cycle_hotkey_rotates_across_clients():
    """Tab / Shift+Tab cycles focus across every connected client."""
    h = await build_bridge()
    try:
        server = _server_monitors([_1080P])
        await _connect(h, "client_a", _bindings_for(_edge_placement("left"), server))
        await _connect(h, "client_b", _bindings_for(_edge_placement("right"), server))
        listener = h.server.listener
        order = list(listener._active_clients_snapshot)  # insertion order

        async def _cycle(direction):
            await h.server_bus.dispatch(
                event_type=BusEventType.SCREEN_SWITCH_CYCLE_REQUEST,
                data=ScreenSwitchCycleRequestEvent(direction=direction),
            )
            await h.wait_until(lambda: not listener._handling_cross_screen)
            await h.settle(10)

        # Forward twice: index -1 -> 0 -> 1.
        await _cycle(1)
        assert listener._active_client_uid == order[0]
        await _cycle(1)
        assert listener._active_client_uid == order[1]
        # Backward once: index 1 -> 0.
        await _cycle(-1)
        assert listener._active_client_uid == order[0]
    finally:
        await h.stop()


@pytest.mark.anyio
async def test_switch_to_server_force_deactivates_client():
    """A server FORCE_SCREEN_CHANGE deactivates the active client."""
    h = await build_bridge()
    try:
        server = _server_monitors([_1080P])
        await _connect(h, h.client_uid, _bindings_for(_edge_placement("right"), server))

        # Activate via a directional hotkey first.
        await h.server_bus.dispatch(
            event_type=BusEventType.SCREEN_SWITCH_DIRECTIONAL_REQUEST,
            data=ScreenSwitchDirectionalRequestEvent(edge=ScreenEdge.RIGHT),
        )
        await h.wait_until(lambda: h.client.mouse._is_active)

        # The server's switch-to-server hotkey sends FORCE_SCREEN_CHANGE.
        await h.server.kbd_listener.command_stream.send(ForceScreenChangeCommandEvent())
        await h.wait_until(lambda: not h.client.mouse._is_active)

        assert h.client.mouse._is_active is False
    finally:
        await h.stop()


@pytest.mark.anyio
async def test_active_client_losing_bindings_returns_to_server():
    """Stranded-active recovery: when the active client loses every
    server-abutting edge binding at runtime (a placed monitor was removed),
    the return-to-server MUST route through SCREEN_CHANGE_GUARD - the guard
    is what runs the per-OS teardown (on macOS: re-couple mouse + reveal the
    hidden cursor). A bare ACTIVE_SCREEN_CHANGED skips it and strands the
    user with an invisible, decoupled pointer. It must also deactivate the
    client, matching the manual switch-to-server path."""
    h = await build_bridge()
    try:
        server = _server_monitors([_1080P])
        await _connect(h, h.client_uid, _bindings_for(_edge_placement("right"), server))
        listener = h.server.listener

        # Activate the client via a directional hotkey.
        await h.server_bus.dispatch(
            event_type=BusEventType.SCREEN_SWITCH_DIRECTIONAL_REQUEST,
            data=ScreenSwitchDirectionalRequestEvent(edge=ScreenEdge.RIGHT),
        )
        await h.wait_until(lambda: h.client.mouse._is_active)
        assert listener._active_client_uid == h.client_uid

        guard_events = h.track(h.server_bus, BusEventType.SCREEN_CHANGE_GUARD)

        # The active client's placed monitor is removed -> its recomputed
        # edge bindings are now empty.
        await h.server_bus.dispatch(
            event_type=BusEventType.CLIENT_LAYOUT_UPDATED,
            data=ClientLayoutUpdatedEvent(client_uid=h.client_uid, edge_bindings=[]),
        )
        await h.wait_until(lambda: not h.client.mouse._is_active)

        # The return-to-server went through the guard (cursor-restore path)...
        assert any(
            data is not None and data.active_screen is None
            for _et, data in guard_events
        ), "stranded return must dispatch SCREEN_CHANGE_GUARD(active_screen=None)"
        # ...and the client was deactivated.
        assert h.client.mouse._is_active is False
    finally:
        await h.stop()
