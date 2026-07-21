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
"""Runtime monitor-change reconciliation across the server<->client bridge."""

import pytest

from event import (
    BusEventType,
    ClientMonitorsUpdateCommandEvent,
)
from model.client import ClientObj
from model.monitor import MonitorInfo

from tests.integration.harness import build_bridge


# Server: one 1920x1080 display at origin. Client: two 1920x1080 monitors
# side by side in OS space; monitor 1 is placed off the server's RIGHT edge.
_SERVER_BBOXES = ((0, 0, 1920, 1080),)
_CLIENT_TWO = ((0, 0, 1920, 1080), (1920, 0, 3840, 1080))


def _client_with_two_monitors(uid="client1"):
    monitors = [
        MonitorInfo(
            monitor_id=0, min_x=0, min_y=0, max_x=1920, max_y=1080, is_primary=True
        ),
        MonitorInfo(monitor_id=1, min_x=1920, min_y=0, max_x=3840, max_y=1080),
    ]
    # Monitor 1 placed to the RIGHT of the server monitor (abuts its right
    # edge at workspace_x=1920); monitor 0 unplaced.
    placements = [
        {
            "client_monitor_id": 1,
            "workspace_x": 1920,
            "workspace_y": 0,
            "width": 1920,
            "height": 1080,
        }
    ]
    return ClientObj(
        uid=uid,
        hostname="clienthost",
        monitors=monitors,
        placements=placements,
    )


def _monitor0_only_dicts():
    return [
        MonitorInfo(
            monitor_id=0, min_x=0, min_y=0, max_x=1920, max_y=1080, is_primary=True
        ).to_dict()
    ]


async def _send_monitors_update(h, monitors, uid=None):
    """Drive a real CLIENT_MONITORS_UPDATE from the client over the bridge."""
    uid = uid or h.client_uid
    await h.client.command_handler.stream.send(
        ClientMonitorsUpdateCommandEvent(
            source=uid,
            client_uid=uid,
            monitors=monitors,
        )
    )
    await h.settle(30)


@pytest.mark.anyio
async def test_client_reports_monitor_change_triggers_server_reconcile():
    """Removing a placed monitor orphans its placement server-side."""
    h = await build_bridge(server_bboxes=_SERVER_BBOXES, client_bboxes=_CLIENT_TWO)
    try:
        client_obj = _client_with_two_monitors(h.client_uid)
        h.enable_server_reconciler(client_obj)
        events = h.track(
            h.server_bus,
            BusEventType.CLIENT_MONITORS_UPDATED,
            BusEventType.CLIENT_LAYOUT_UPDATED,
        )

        with h.server_geometry(_SERVER_BBOXES):
            await _send_monitors_update(h, _monitor0_only_dicts())

        # The placement pointing at the now-gone monitor 1 was dropped.
        assert client_obj.placements == []
        assert [m.monitor_id for m in client_obj.monitors] == [0]
        # Both bus events fired on the server.
        seen = {et for et, _ in events}
        assert BusEventType.CLIENT_MONITORS_UPDATED in seen
        assert BusEventType.CLIENT_LAYOUT_UPDATED in seen
    finally:
        await h.stop()


@pytest.mark.anyio
async def test_reconcile_forces_return_when_active_client_bindings_empty():
    """Active client losing all edge bindings is forced back to the server."""
    h = await build_bridge(server_bboxes=_SERVER_BBOXES, client_bboxes=_CLIENT_TWO)
    try:
        client_obj = _client_with_two_monitors(h.client_uid)
        h.enable_server_reconciler(client_obj)

        # Mark the server listener as currently focused on this client.
        h.server.listener._active_clients = {h.client_uid: True}
        h.server.listener._active_client_uid = h.client_uid
        h.server.listener._listening = True

        events = h.track(h.server_bus, BusEventType.ACTIVE_SCREEN_CHANGED)

        # Monitor 1 vanishes -> its placement (the only one, adjacent to the
        # server) is dropped -> the active client has no bindings left.
        with h.server_geometry(_SERVER_BBOXES):
            await _send_monitors_update(h, _monitor0_only_dicts())

        forced = [
            data
            for et, data in events
            if et == BusEventType.ACTIVE_SCREEN_CHANGED and data.active_screen is None
        ]
        assert forced, "empty bindings on the active client must force a return"
        assert h.server.listener._active_client_uid is None
        assert h.server.listener._listening is False
    finally:
        await h.stop()


@pytest.mark.anyio
async def test_reconcile_repushes_topology_when_bindings_survive():
    """Surviving bindings trigger a topology re-push, not a return."""
    h = await build_bridge(server_bboxes=_SERVER_BBOXES, client_bboxes=_CLIENT_TWO)
    try:
        client_obj = _client_with_two_monitors(h.client_uid)
        h.enable_server_reconciler(client_obj)

        # Activate the client on the server side so _on_client_layout_updated
        # takes the "keeps bindings -> re-push topology" branch.
        h.server.listener._active_clients = {h.client_uid: True}
        h.server.listener._active_client_uid = h.client_uid
        h.server.listener._listening = True
        # And activate the client controller so it accepts the topology.
        from event import ClientActiveEvent

        await h.client_bus.dispatch(
            event_type=BusEventType.CLIENT_ACTIVE,
            data=ClientActiveEvent(client_uid=h.client_uid, client_monitor_id=1),
        )
        await h.settle()

        events = h.track(h.server_bus, BusEventType.ACTIVE_SCREEN_CHANGED)

        # Monitor 1 stays but its resolution changes (still adjacent to the
        # server) -> signature changes, placement kept, bindings survive.
        changed = [
            MonitorInfo(
                monitor_id=0, min_x=0, min_y=0, max_x=1920, max_y=1080, is_primary=True
            ).to_dict(),
            MonitorInfo(
                monitor_id=1, min_x=1920, min_y=0, max_x=3840, max_y=1200
            ).to_dict(),
        ]
        with h.server_geometry(_SERVER_BBOXES):
            await _send_monitors_update(h, changed)
        await h.settle(30)

        # No forced return.
        assert not [
            d
            for et, d in events
            if et == BusEventType.ACTIVE_SCREEN_CHANGED and d.active_screen is None
        ]
        # The placement survived.
        assert any(p["client_monitor_id"] == 1 for p in client_obj.placements)
        # Client received the refreshed topology over the command stream.
        assert h.client.mouse._edge_bindings, "topology should have been re-pushed"
        assert h.client.mouse._server_bbox == (0, 0, 1920, 1080)
    finally:
        await h.stop()


@pytest.mark.anyio
async def test_client_local_monitor_hotplug_stranded_recovery():
    """Client's active monitor vanishing forces a return-to-server."""
    h = await build_bridge(server_bboxes=_SERVER_BBOXES, client_bboxes=_CLIENT_TWO)
    try:
        # Client active on its monitor 1.
        from event import ClientActiveEvent

        await h.client_bus.dispatch(
            event_type=BusEventType.CLIENT_ACTIVE,
            data=ClientActiveEvent(client_uid=h.client_uid, client_monitor_id=1),
        )
        await h.settle()
        assert h.client.mouse._is_active is True

        # Also mark the server as focused on the client so we can observe
        # the return landing server-side.
        h.server.listener._active_clients = {h.client_uid: True}
        h.server.listener._active_client_uid = h.client_uid
        h.server.listener._listening = True
        events = h.track(h.server_bus, BusEventType.ACTIVE_SCREEN_CHANGED)

        # Monitor 1 disappears locally on the client.
        with h.client_geometry(_SERVER_BBOXES):
            await h.client.mouse._on_local_monitors_updated(None)
        await h.settle(30)

        # Client relinquished control...
        assert h.client.mouse._is_active is False
        # ...and the server received the return over the command bridge.
        returned = [
            d
            for et, d in events
            if et == BusEventType.ACTIVE_SCREEN_CHANGED and d.active_screen is None
        ]
        assert returned, "stranded client must hand control back to the server"
        assert h.server.listener._active_client_uid is None
    finally:
        await h.stop()


@pytest.mark.anyio
async def test_client_intra_monitor_warp_between_own_monitors():
    """Cursor crosses between two of the client's own adjacent monitors.

    With an intra-client binding (client monitor 0 RIGHT edge -> monitor 1
    LEFT edge) pushed by the server, pushing the cursor off monitor 0's
    right edge warps it onto monitor 1 - it stays on the client, no
    return-to-server crossing.
    """
    h = await build_bridge(server_bboxes=_SERVER_BBOXES, client_bboxes=_CLIENT_TWO)
    try:
        from event import ClientActiveEvent, ClientTopologyCommandEvent

        # Server pushes the topology over the real command stream: no
        # server-abutting edge bindings, one intra-client warp binding.
        intra = {
            "src_monitor_id": 0,
            "src_edge": "right",
            "src_axis_start": 0.0,
            "src_axis_end": 1.0,
            "dst_monitor_id": 1,
            "dst_edge": "left",
            "dst_axis_start": 0.0,
            "dst_axis_end": 1.0,
            "dst_monitor_min_x": 1920,
            "dst_monitor_min_y": 0,
            "dst_monitor_max_x": 3840,
            "dst_monitor_max_y": 1080,
        }
        await h.server.command_handler.stream.send(
            ClientTopologyCommandEvent(
                target=h.client_uid,
                edge_bindings=[],
                server_bbox=(0, 0, 1920, 1080),
                intra_client_bindings=[intra],
            )
        )
        await h.settle()
        assert h.client.mouse._intra_pairs == {(0, 1)}

        # Client active on its monitor 0.
        await h.client_bus.dispatch(
            event_type=BusEventType.CLIENT_ACTIVE,
            data=ClientActiveEvent(client_uid=h.client_uid, client_monitor_id=0),
        )
        await h.settle()

        server_events = h.track(h.server_bus, BusEventType.ACTIVE_SCREEN_CHANGED)

        # Push the cursor toward monitor 0's right edge.
        ctrl = h.client.mouse
        for x in range(1905, 1920, 2):
            ctrl._movement_history.append((x, 540))
        h.client.mouse_mock.position = (1919, 540)

        await ctrl._check_edge()
        await h.settle(10)

        # Warped just inside monitor 1 (dst LEFT edge -> min_x + 1).
        warped_x, warped_y = h.client.mouse_mock.position
        assert 1920 <= warped_x < 3840
        assert warped_x == 1921
        # Now tracked as being on monitor 1, still active, no return.
        assert ctrl._active_monitor_id == 1
        assert ctrl._is_active is True
        assert not [
            d
            for et, d in server_events
            if et == BusEventType.ACTIVE_SCREEN_CHANGED and d.active_screen is None
        ], "an intra-client warp must not hand control back to the server"
    finally:
        await h.stop()


@pytest.mark.anyio
async def test_client_local_monitor_hotplug_survivor_no_return():
    """A surviving active monitor re-resolves geometry without returning."""
    h = await build_bridge(server_bboxes=_SERVER_BBOXES, client_bboxes=_CLIENT_TWO)
    try:
        from event import ClientActiveEvent

        # Active on monitor 0, which survives the hotplug below.
        await h.client_bus.dispatch(
            event_type=BusEventType.CLIENT_ACTIVE,
            data=ClientActiveEvent(client_uid=h.client_uid, client_monitor_id=0),
        )
        await h.settle()
        assert h.client.mouse._is_active is True

        events = h.track(h.client_bus, BusEventType.CLIENT_INACTIVE)

        # Monitor 1 drops but monitor 0 (the active one) stays.
        with h.client_geometry(_SERVER_BBOXES):
            await h.client.mouse._on_local_monitors_updated(None)
        await h.settle(20)

        assert h.client.mouse._is_active is True
        assert not events, "surviving active monitor must not force a return"
        # Geometry re-resolved to monitor 0's OS bbox.
        assert h.client.mouse._active_target_bbox == (0, 0, 1920, 1080)
    finally:
        await h.stop()


def _single_monitor_client(uid, hostname):
    """Client with one monitor placed off the server's LEFT edge."""
    return ClientObj(
        uid=uid,
        hostname=hostname,
        monitors=[
            MonitorInfo(
                monitor_id=0, min_x=0, min_y=0, max_x=1920, max_y=1080, is_primary=True
            )
        ],
        placements=[
            {
                "client_monitor_id": 0,
                "workspace_x": -1920,
                "workspace_y": 0,
                "width": 1920,
                "height": 1080,
            }
        ],
    )


@pytest.mark.anyio
async def test_multi_client_reconcile_isolates_other_client():
    """A monitor change on one client must not disturb another client.

    Two clients are registered on the server. Only the reporting client's
    stored monitors/placements are reconciled; the untouched client's state
    stays intact.
    """
    h = await build_bridge(server_bboxes=_SERVER_BBOXES, client_bboxes=_CLIENT_TWO)
    try:
        client_a = _client_with_two_monitors(uid="client_a")
        client_a.host_name = "hosta"
        client_b = _single_monitor_client("client_b", "hostb")
        h.enable_server_reconciler(client_a, client_b)

        events = h.track(
            h.server_bus,
            BusEventType.CLIENT_MONITORS_UPDATED,
            BusEventType.CLIENT_LAYOUT_UPDATED,
        )

        # Client A loses its placed monitor 1.
        with h.server_geometry(_SERVER_BBOXES):
            await _send_monitors_update(h, _monitor0_only_dicts(), uid="client_a")

        # A reconciled: placement dropped, monitor list trimmed.
        assert client_a.placements == []
        assert [m.monitor_id for m in client_a.monitors] == [0]
        # B untouched: same placement, same monitors.
        assert len(client_b.placements) == 1
        assert client_b.placements[0]["client_monitor_id"] == 0
        assert [m.monitor_id for m in client_b.monitors] == [0]

        seen = {et for et, _ in events}
        assert BusEventType.CLIENT_MONITORS_UPDATED in seen
        assert BusEventType.CLIENT_LAYOUT_UPDATED in seen
    finally:
        await h.stop()
