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
"""Complex cross-screen topology routing over the server<->client bridge.

Every binding here comes from the production ``compute_edge_bindings``
math applied to real placements + server monitors, so these tests
exercise the whole spatial pipeline (adjacency detection, axis-segment
mapping, per-monitor edge resolution) end-to-end, not hand-tuned dicts.
"""

import pytest

from event import (
    BusEventType,
    ActiveScreenChangedEvent,
    ClientActiveEvent,
    ClientConnectedEvent,
    ClientLayoutUpdatedEvent,
    ClientTopologyCommandEvent,
)
from model.monitor import MonitorInfo
from network.stream import StreamType
from utils.screen import compute_edge_bindings

from tests.integration.harness import build_bridge


_1080P = (0, 0, 1920, 1080)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _server_monitors(bboxes, primary_index=0):
    return [
        MonitorInfo(
            monitor_id=i,
            min_x=a,
            min_y=b,
            max_x=c,
            max_y=d,
            is_primary=(i == primary_index),
        )
        for i, (a, b, c, d) in enumerate(bboxes)
    ]


def _bindings_for(placement, server_monitors):
    """Real edge bindings for one placement (as wire dicts)."""
    return [eb.to_dict() for eb in compute_edge_bindings(placement, server_monitors)]


async def _connect(h, uid, edge_bindings):
    await h.server_bus.dispatch(
        event_type=BusEventType.CLIENT_CONNECTED,
        data=ClientConnectedEvent(
            client_uid=uid,
            streams=[StreamType.MOUSE, StreamType.KEYBOARD],
            edge_bindings=edge_bindings,
        ),
    )
    await h.settle()


def _drive_edge(listener, edge, monitor_bbox, sec):
    """Feed the server listener a directed push into ``edge`` of a monitor.

    ``sec`` is the secondary-axis coordinate (Y for LEFT/RIGHT, X for
    TOP/BOTTOM) that selects which edge segment / band the crossing hits.
    """
    mnx, mny, mxx, mxy = monitor_bbox
    if edge == "right":
        for x in range(mxx - 13, mxx - 1, 2):
            listener.on_move(x, sec)
        listener.on_move(mxx - 1, sec)
    elif edge == "left":
        for x in range(mnx + 12, mnx, -2):
            listener.on_move(x, sec)
        listener.on_move(mnx, sec)
    elif edge == "top":
        for y in range(mny + 12, mny, -2):
            listener.on_move(sec, y)
        listener.on_move(sec, mny)
    elif edge == "bottom":
        for y in range(mxy - 13, mxy - 1, 2):
            listener.on_move(sec, y)
        listener.on_move(sec, mxy - 1)
    else:
        raise ValueError(edge)


async def _cross(h, edge, monitor_bbox, sec, expected_uid):
    """Drive a server edge crossing and wait for it to fully settle.

    Waits both for the target to become active AND for the in-flight
    ``_handling_cross_screen`` guard to clear, so a subsequent crossing in
    the same test isn't swallowed by the "already handling" fast-path.
    """
    listener = h.server.listener
    _drive_edge(listener, edge, monitor_bbox, sec)
    await h.wait_until(lambda: listener._active_client_uid == expected_uid)
    await h.wait_until(lambda: not listener._handling_cross_screen)
    await h.settle(20)


async def _return_to_server(h):
    """Hand control back to the server and let the listener buffer again."""
    await h.wait_until(lambda: not h.server.listener._handling_cross_screen)
    await h.server_bus.dispatch(
        event_type=BusEventType.ACTIVE_SCREEN_CHANGED,
        data=ActiveScreenChangedEvent(active_screen=None),
    )
    await h.settle()


async def _no_cross(h, edge, monitor_bbox, sec):
    """Drive an edge push that must NOT trigger a crossing."""
    _drive_edge(h.server.listener, edge, monitor_bbox, sec)
    await h.settle(20)


# ---------------------------------------------------------------------------
# Multi-client routing on a single server monitor
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_two_clients_on_opposite_edges_route_independently():
    """LEFT/RIGHT crossings each resolve to the client on that edge."""
    h = await build_bridge()
    try:
        server = _server_monitors([_1080P])
        left = {
            "client_monitor_id": 0,
            "workspace_x": -1920,
            "workspace_y": 0,
            "width": 1920,
            "height": 1080,
        }
        right = {
            "client_monitor_id": 0,
            "workspace_x": 1920,
            "workspace_y": 0,
            "width": 1920,
            "height": 1080,
        }
        await _connect(h, "client_left", _bindings_for(left, server))
        await _connect(h, "client_right", _bindings_for(right, server))

        listener = h.server.listener

        await _cross(h, "right", _1080P, 540, "client_right")
        assert listener._active_client_uid == "client_right"

        await _return_to_server(h)
        await _cross(h, "left", _1080P, 540, "client_left")
        assert listener._active_client_uid == "client_left"

        # Both clients stayed registered the whole time.
        assert set(listener._active_clients_snapshot) == {
            "client_left",
            "client_right",
        }
    finally:
        await h.stop()


@pytest.mark.anyio
async def test_right_edge_split_into_two_bands():
    """The RIGHT edge split top/bottom routes by the crossing's Y band."""
    h = await build_bridge()
    try:
        server = _server_monitors([_1080P])
        top = {
            "client_monitor_id": 0,
            "workspace_x": 1920,
            "workspace_y": 0,
            "width": 1920,
            "height": 540,
        }
        bot = {
            "client_monitor_id": 0,
            "workspace_x": 1920,
            "workspace_y": 540,
            "width": 1920,
            "height": 540,
        }
        top_b = _bindings_for(top, server)
        bot_b = _bindings_for(bot, server)
        assert top_b[0]["server_axis_end"] == pytest.approx(0.5)
        assert bot_b[0]["server_axis_start"] == pytest.approx(0.5)
        await _connect(h, "client_top", top_b)
        await _connect(h, "client_bot", bot_b)

        listener = h.server.listener

        await _cross(h, "right", _1080P, 200, "client_top")  # top band
        assert listener._active_client_uid == "client_top"

        await _return_to_server(h)
        await _cross(h, "right", _1080P, 900, "client_bot")  # bottom band
        assert listener._active_client_uid == "client_bot"
    finally:
        await h.stop()


@pytest.mark.anyio
async def test_band_gap_edge_does_not_cross():
    """A crossing outside every bound segment activates no client."""
    h = await build_bridge()
    try:
        server = _server_monitors([_1080P])
        # Only the top third of the RIGHT edge is bound.
        top = {
            "client_monitor_id": 0,
            "workspace_x": 1920,
            "workspace_y": 0,
            "width": 1920,
            "height": 360,
        }
        await _connect(h, "client_top", _bindings_for(top, server))

        # Cross well below the bound band (axis 0.83, band is [0, 0.33)).
        await _no_cross(h, "right", _1080P, 900)

        assert h.server.listener._active_client_uid is None
        assert h.server.listener._listening is False
    finally:
        await h.stop()


# ---------------------------------------------------------------------------
# Multi-monitor server
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_secondary_server_monitor_outer_edge():
    """A client off the SECONDARY server monitor's outer edge is reachable.

    The primary monitor's shared (interior) edge must not cross, while the
    secondary's outer edge routes to the client bound there.
    """
    server_bboxes = [(0, 0, 1920, 1080), (1920, 0, 3840, 1080)]
    h = await build_bridge(server_bboxes=tuple(server_bboxes))
    try:
        server = _server_monitors(server_bboxes)
        placement = {
            "client_monitor_id": 0,
            "workspace_x": 3840,
            "workspace_y": 0,
            "width": 1920,
            "height": 1080,
        }
        bindings = _bindings_for(placement, server)
        assert bindings and bindings[0]["server_monitor_id"] == 1
        await _connect(h, "client_far", bindings)

        listener = h.server.listener

        # Interior edge (primary's RIGHT, x=1919) abuts monitor 1 -> no cross.
        await _no_cross(h, "right", (0, 0, 1920, 1080), 540)
        assert listener._active_client_uid is None

        # Outer edge of the secondary monitor (x=3839) -> cross.
        await _cross(h, "right", (1920, 0, 3840, 1080), 540, "client_far")
        assert listener._active_client_uid == "client_far"
    finally:
        await h.stop()


@pytest.mark.anyio
async def test_corner_placement_spans_two_server_edges():
    """A placement abutting two server edges routes from either edge.

    Server monitors form an L: primary (top-left) + a monitor below-right.
    A client placed in the top-right void touches the primary's RIGHT edge
    and the lower monitor's TOP edge; both route to it.
    """
    server_bboxes = [(0, 0, 1920, 1080), (1920, 1080, 3840, 2160)]
    h = await build_bridge(server_bboxes=tuple(server_bboxes))
    try:
        server = _server_monitors(server_bboxes)
        placement = {
            "client_monitor_id": 0,
            "workspace_x": 1920,
            "workspace_y": 0,
            "width": 1920,
            "height": 1080,
        }
        bindings = _bindings_for(placement, server)
        server_edges = sorted(b["server_edge"] for b in bindings)
        assert server_edges == ["right", "top"], server_edges
        await _connect(h, "client_corner", bindings)

        listener = h.server.listener

        # Via the primary monitor's RIGHT edge.
        await _cross(h, "right", (0, 0, 1920, 1080), 540, "client_corner")
        assert listener._active_client_uid == "client_corner"

        await _return_to_server(h)

        # Via the lower monitor's TOP edge (x within [1920, 3840)).
        await _cross(h, "top", (1920, 1080, 3840, 2160), 2880, "client_corner")
        assert listener._active_client_uid == "client_corner"
    finally:
        await h.stop()


# ---------------------------------------------------------------------------
# Vertical crossing, full E2E onto the real client controller
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_top_edge_crossing_activates_and_lands_client():
    """Crossing the server's TOP edge activates the client above it."""
    h = await build_bridge()
    try:
        server = _server_monitors([_1080P])
        placement = {
            "client_monitor_id": 0,
            "workspace_x": 0,
            "workspace_y": -1080,
            "width": 1920,
            "height": 1080,
        }
        bindings = _bindings_for(placement, server)
        assert bindings[0]["server_edge"] == "top"
        await _connect(h, h.client_uid, bindings)

        listener = h.server.listener
        _drive_edge(listener, "top", _1080P, 960)  # cross upward at x=960
        await h.wait_until(lambda: h.client.mouse._is_active)
        await h.settle(30)

        assert h.client.mouse._is_active is True
        assert listener._active_client_uid == h.client_uid
        assert h.client.mouse._edge_bindings
        # A TOP crossing lands near the client's bottom edge.
        _, cy = h.client.mouse_mock.position
        assert cy >= 1000
    finally:
        await h.stop()


# ---------------------------------------------------------------------------
# Intra-client warp chain across three monitors
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_intra_client_warp_chain_three_monitors():
    """Cursor warps 0->1 then 1->2 across three adjacent client monitors."""
    client_bboxes = (
        (0, 0, 1920, 1080),
        (1920, 0, 3840, 1080),
        (3840, 0, 5760, 1080),
    )
    h = await build_bridge(client_bboxes=client_bboxes)
    try:

        def _intra(src, src_edge, dst, dst_edge, dbbox):
            return {
                "src_monitor_id": src,
                "src_edge": src_edge,
                "src_axis_start": 0.0,
                "src_axis_end": 1.0,
                "dst_monitor_id": dst,
                "dst_edge": dst_edge,
                "dst_axis_start": 0.0,
                "dst_axis_end": 1.0,
                "dst_monitor_min_x": dbbox[0],
                "dst_monitor_min_y": dbbox[1],
                "dst_monitor_max_x": dbbox[2],
                "dst_monitor_max_y": dbbox[3],
            }

        intra = [
            _intra(0, "right", 1, "left", (1920, 0, 3840, 1080)),
            _intra(1, "right", 2, "left", (3840, 0, 5760, 1080)),
        ]
        await h.server.command_handler.stream.send(
            ClientTopologyCommandEvent(
                target=h.client_uid,
                edge_bindings=[],
                server_bbox=(0, 0, 1920, 1080),
                intra_client_bindings=intra,
            )
        )
        await h.settle()
        assert h.client.mouse._intra_pairs == {(0, 1), (1, 2)}

        await h.client_bus.dispatch(
            event_type=BusEventType.CLIENT_ACTIVE,
            data=ClientActiveEvent(client_uid=h.client_uid, client_monitor_id=0),
        )
        await h.settle()
        ctrl = h.client.mouse
        server_events = h.track(h.server_bus, BusEventType.ACTIVE_SCREEN_CHANGED)

        # Monitor 0 right edge -> warp onto monitor 1.
        for x in range(1905, 1920, 2):
            ctrl._movement_history.append((x, 540))
        h.client.mouse_mock.position = (1919, 540)
        await ctrl._check_edge()
        await h.settle(10)
        assert ctrl._active_monitor_id == 1
        assert h.client.mouse_mock.position[0] == 1921

        # Monitor 1 right edge -> warp onto monitor 2.
        for x in range(3825, 3840, 2):
            ctrl._movement_history.append((x, 540))
        h.client.mouse_mock.position = (3839, 540)
        await ctrl._check_edge()
        await h.settle(10)
        assert ctrl._active_monitor_id == 2
        assert h.client.mouse_mock.position[0] == 3841

        assert not [
            d
            for et, d in server_events
            if et == BusEventType.ACTIVE_SCREEN_CHANGED and d.active_screen is None
        ]
        assert ctrl._is_active is True
    finally:
        await h.stop()


# ---------------------------------------------------------------------------
# Return-to-server from a specific monitor of a multi-monitor client
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_return_to_server_from_placed_secondary_client_monitor():
    """A two-monitor client returns to server off the placed monitor's edge."""
    client_bboxes = ((0, 0, 1920, 1080), (1920, 0, 3840, 1080))
    h = await build_bridge(client_bboxes=client_bboxes)
    try:
        server = _server_monitors([_1080P])
        placement = {
            "client_monitor_id": 1,
            "workspace_x": 1920,
            "workspace_y": 0,
            "width": 1920,
            "height": 1080,
        }
        bindings = _bindings_for(placement, server)
        assert bindings[0]["client_monitor_id"] == 1
        await _connect(h, h.client_uid, bindings)

        listener = h.server.listener
        _drive_edge(listener, "right", _1080P, 540)
        await h.wait_until(lambda: h.client.mouse._is_active)
        await h.settle(30)

        ctrl = h.client.mouse
        assert ctrl._active_monitor_id == 1
        assert ctrl._active_target_bbox == (1920, 0, 3840, 1080)
        assert ctrl._edge_bindings

        server_events = h.track(h.server_bus, BusEventType.ACTIVE_SCREEN_CHANGED)

        # Push toward monitor 1's LEFT edge (bound back to the server RIGHT).
        for x in range(1932, 1920, -2):
            ctrl._movement_history.append((x, 540))
        h.client.mouse_mock.position = (1920, 540)
        await ctrl._check_edge()
        await h.settle(20)

        assert ctrl._is_active is False
        returned = [
            d
            for et, d in server_events
            if et == BusEventType.ACTIVE_SCREEN_CHANGED and d.active_screen is None
        ]
        assert returned, "client must hand control back to the server"
        assert listener._active_client_uid is None
    finally:
        await h.stop()


# ---------------------------------------------------------------------------
# Multiple clients connected simultaneously + runtime topology sync
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_multi_client_coexistence_crossing_and_topology_sync():
    """Several clients coexist; crossings route correctly and a runtime
    layout change on the active client is pushed straight to it."""
    # The real (E2E) client lives off the RIGHT edge; a routing-only peer
    # lives off the LEFT edge.
    h = await build_bridge(client_uid="client_right")
    try:
        server = _server_monitors([_1080P])
        left = {
            "client_monitor_id": 0,
            "workspace_x": -1920,
            "workspace_y": 0,
            "width": 1920,
            "height": 1080,
        }
        right = {
            "client_monitor_id": 0,
            "workspace_x": 1920,
            "workspace_y": 0,
            "width": 1920,
            "height": 1080,
        }
        right_bindings = _bindings_for(right, server)
        await _connect(h, "client_left", _bindings_for(left, server))
        await _connect(h, "client_right", right_bindings)

        listener = h.server.listener
        assert set(listener._active_clients_snapshot) == {
            "client_left",
            "client_right",
        }

        # Cross RIGHT: the real client activates and receives its topology.
        await _cross(h, "right", _1080P, 540, "client_right")
        assert h.client.mouse._is_active is True
        # Original binding: full RIGHT edge -> client LEFT edge, band [0, 1).
        assert h.client.mouse._edge_bindings
        assert h.client.mouse._edge_bindings[0]["server_axis_end"] == pytest.approx(1.0)

        # --- runtime topology change on the ACTIVE client ---------------
        # Admin re-places client_right so only the top half of the server's
        # RIGHT edge maps to it. Server hot-reloads its cache and, because
        # the client is active, re-pushes the fresh topology over the bridge.
        new_right = {
            "client_monitor_id": 0,
            "workspace_x": 1920,
            "workspace_y": 0,
            "width": 1920,
            "height": 540,
        }
        new_bindings = _bindings_for(new_right, server)
        assert new_bindings[0]["server_axis_end"] == pytest.approx(0.5)
        await h.server_bus.dispatch(
            event_type=BusEventType.CLIENT_LAYOUT_UPDATED,
            data=ClientLayoutUpdatedEvent(
                client_uid="client_right",
                edge_bindings=new_bindings,
            ),
        )
        await h.wait_until(
            lambda: (
                h.client.mouse._edge_bindings
                and h.client.mouse._edge_bindings[0]["server_axis_end"] == 0.5
            )
        )
        # The client received the refreshed topology without a reconnect.
        assert h.client.mouse._edge_bindings[0]["server_axis_end"] == pytest.approx(0.5)
        # The server's own routing cache updated too.
        cached = dict(listener._edge_bindings_snapshot)["client_right"]
        assert cached[0]["server_axis_end"] == pytest.approx(0.5)

        # Return, then confirm the other client is still routable.
        await _return_to_server(h)
        await _cross(h, "left", _1080P, 540, "client_left")
        assert listener._active_client_uid == "client_left"
        assert set(listener._active_clients_snapshot) == {
            "client_left",
            "client_right",
        }
    finally:
        await h.stop()


# ---------------------------------------------------------------------------
# One client whose two monitors straddle two different server edges
# ---------------------------------------------------------------------------


# Client with two OS monitors side by side; monitor 0 will be placed off the
# server's LEFT edge, monitor 1 off the server's RIGHT edge.
_CLIENT_TWO_OS = ((0, 0, 1920, 1080), (1920, 0, 3840, 1080))


def _two_edge_client_bindings(server):
    """Bindings for a client whose m0 -> server LEFT, m1 -> server RIGHT."""
    m0_left = {
        "client_monitor_id": 0,
        "workspace_x": -1920,
        "workspace_y": 0,
        "width": 1920,
        "height": 1080,
    }
    m1_right = {
        "client_monitor_id": 1,
        "workspace_x": 1920,
        "workspace_y": 0,
        "width": 1920,
        "height": 1080,
    }
    return _bindings_for(m0_left, server) + _bindings_for(m1_right, server)


@pytest.mark.anyio
async def test_client_two_monitors_on_different_server_edges():
    """One client, two monitors, each abutting a different server edge.

    Crossing the server's LEFT edge lands on the client's monitor 0;
    crossing the RIGHT edge lands on its monitor 1. The activation packet
    carries the right ``client_monitor_id`` each time, so the injector's
    target bbox tracks the correct local monitor.
    """
    h = await build_bridge(client_bboxes=_CLIENT_TWO_OS)
    try:
        server = _server_monitors([_1080P])
        bindings = _two_edge_client_bindings(server)
        # One binding per server edge, routed to distinct client monitors.
        by_edge = {b["server_edge"]: b for b in bindings}
        assert by_edge["left"]["client_monitor_id"] == 0
        assert by_edge["right"]["client_monitor_id"] == 1
        await _connect(h, h.client_uid, bindings)

        ctrl = h.client.mouse

        # Cross LEFT -> client monitor 0.
        await _cross(h, "left", _1080P, 540, h.client_uid)
        await h.wait_until(lambda: ctrl._is_active and ctrl._active_monitor_id == 0)
        assert ctrl._active_monitor_id == 0
        assert ctrl._active_target_bbox == (0, 0, 1920, 1080)

        await _return_to_server(h)

        # Cross RIGHT -> client monitor 1.
        await _cross(h, "right", _1080P, 540, h.client_uid)
        await h.wait_until(lambda: ctrl._is_active and ctrl._active_monitor_id == 1)
        assert ctrl._active_monitor_id == 1
        assert ctrl._active_target_bbox == (1920, 0, 3840, 1080)
    finally:
        await h.stop()


@pytest.mark.anyio
async def test_two_edge_client_plus_second_client():
    """The two-edge client coexists with a third client on the TOP edge.

    Routing stays correct across all three targets: LEFT -> real client's
    monitor 0, RIGHT -> real client's monitor 1, TOP -> the second client.
    """
    h = await build_bridge(client_bboxes=_CLIENT_TWO_OS)
    try:
        server = _server_monitors([_1080P])
        await _connect(h, h.client_uid, _two_edge_client_bindings(server))

        # Second client sits directly ABOVE the server monitor.
        above = {
            "client_monitor_id": 0,
            "workspace_x": 0,
            "workspace_y": -1080,
            "width": 1920,
            "height": 1080,
        }
        await _connect(h, "client_above", _bindings_for(above, server))

        listener = h.server.listener
        assert set(listener._active_clients_snapshot) == {h.client_uid, "client_above"}

        ctrl = h.client.mouse

        # LEFT -> real client, monitor 0.
        await _cross(h, "left", _1080P, 540, h.client_uid)
        await h.wait_until(lambda: ctrl._is_active and ctrl._active_monitor_id == 0)
        assert ctrl._active_monitor_id == 0

        await _return_to_server(h)

        # RIGHT -> real client, monitor 1.
        await _cross(h, "right", _1080P, 540, h.client_uid)
        await h.wait_until(lambda: ctrl._active_monitor_id == 1)
        assert ctrl._active_monitor_id == 1

        await _return_to_server(h)

        # TOP -> the second client (server-side routing).
        await _cross(h, "top", _1080P, 960, "client_above")
        assert listener._active_client_uid == "client_above"

        # All three routing targets remained registered throughout.
        assert set(listener._active_clients_snapshot) == {h.client_uid, "client_above"}
    finally:
        await h.stop()
