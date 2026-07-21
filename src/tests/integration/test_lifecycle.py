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
"""Client connect/disconnect lifecycle over the server<->client bridge."""

import pytest

from event import BusEventType, ClientDisconnectedEvent
from network.stream import StreamType

from tests.integration.harness import build_bridge
from tests.integration.test_topology import (
    _1080P,
    _bindings_for,
    _connect,
    _cross,
    _server_monitors,
)


def _placement(server_edge):
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


async def _disconnect(h, uid):
    await h.server_bus.dispatch(
        event_type=BusEventType.CLIENT_DISCONNECTED,
        data=ClientDisconnectedEvent(client_uid=uid, streams=[StreamType.MOUSE]),
    )
    await h.settle()


@pytest.mark.anyio
async def test_disconnect_active_client_stops_routing():
    """Disconnecting the active client tears down its routing state."""
    h = await build_bridge()
    try:
        server = _server_monitors([_1080P])
        await _connect(h, h.client_uid, _bindings_for(_placement("right"), server))
        await _cross(h, "right", _1080P, 540, h.client_uid)
        assert h.client.mouse._is_active is True

        await _disconnect(h, h.client_uid)

        listener = h.server.listener
        assert h.client_uid not in listener._active_clients_snapshot
        assert h.client_uid not in dict(listener._edge_bindings_snapshot)
        assert listener._listening is False
    finally:
        await h.stop()


@pytest.mark.anyio
async def test_disconnect_one_of_many_leaves_others_routable():
    """Disconnecting one client leaves the others registered and reachable."""
    # Real E2E client on the RIGHT edge; routing-only peer on the LEFT.
    h = await build_bridge(client_uid="client_right")
    try:
        server = _server_monitors([_1080P])
        await _connect(h, "client_left", _bindings_for(_placement("left"), server))
        await _connect(h, "client_right", _bindings_for(_placement("right"), server))
        listener = h.server.listener
        assert set(listener._active_clients_snapshot) == {"client_left", "client_right"}

        # Drop the left peer.
        await _disconnect(h, "client_left")
        assert set(listener._active_clients_snapshot) == {"client_right"}
        assert "client_left" not in dict(listener._edge_bindings_snapshot)

        # The surviving client is still reachable by a crossing.
        await _cross(h, "right", _1080P, 540, "client_right")
        assert listener._active_client_uid == "client_right"
        assert h.client.mouse._is_active is True
    finally:
        await h.stop()
