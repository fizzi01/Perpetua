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
"""Wayland (InputCapture barrier) server path.

The barrier path used to hand-roll its own activation sequence, which is how it
ended up omitting the topology push - and without ``edge_bindings`` /
``server_bbox`` the client's ``_lookup_return_to_server`` bails on its first
guard, so the cursor could reach a client and never come back. These tests pin
that packet sequence, and the barrier geometry that decides where the pointer
may be held at all.
"""

from tests.unit import _MOCK_PYNPUT

from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from event import (
    ClientTopologyCommandEvent,
    CrossScreenCommandEvent,
    MouseEvent,
)

_MOCK_PYNPUT()

from utils.screen import MonitorLayout  # noqa: E402

SCREEN_W, SCREEN_H = 1920, 1080
CLIENT_UID = "client-uid"


def _patch_wayland_geometry():
    """A single 1920x1080 server monitor, on a GNOME Wayland session."""
    layout = MonitorLayout.from_bboxes([(0, 0, SCREEN_W, SCREEN_H)])
    return [
        patch("input.mouse._base.Screen.get_size", return_value=(SCREEN_W, SCREEN_H)),
        patch(
            "input.mouse._base.Screen.get_virtual_bbox",
            return_value=(0, 0, SCREEN_W, SCREEN_H),
        ),
        patch("input.mouse._base.Screen.get_monitor_layout", return_value=layout),
        patch("input.mouse._linux.is_wayland", return_value=True),
        patch("input.mouse._linux.is_gnome", return_value=True),
        patch("input.mouse._linux.is_kde", return_value=False),
    ]


def _binding(
    *,
    server_edge="right",
    client_edge="left",
    server_axis=(0.0, 1.0),
    client_axis=(0.0, 1.0),
    client_monitor_id=7,
):
    """One EdgeBinding, in the shape the layout math produces."""
    return {
        "server_monitor_id": 0,
        "server_edge": server_edge,
        "server_axis_start": server_axis[0],
        "server_axis_end": server_axis[1],
        "client_monitor_id": client_monitor_id,
        "client_edge": client_edge,
        "client_axis_start": client_axis[0],
        "client_axis_end": client_axis[1],
        "server_monitor_min_x": 0,
        "server_monitor_min_y": 0,
        "server_monitor_max_x": SCREEN_W,
        "server_monitor_max_y": SCREEN_H,
    }


@pytest.fixture
def wayland_listener(event_bus):
    """A barrier-mode ``ServerMouseListener`` with one client bound right."""
    with ExitStack() as stack:
        for p in _patch_wayland_geometry():
            stack.enter_context(p)

        # Import inside the patches: _barrier_mode is decided in __init__.
        from input.mouse import _linux

        stack.enter_context(
            patch.object(_linux.ServerMouseListener, "_create_listener", MagicMock())
        )

        stream = AsyncMock()
        command_stream = AsyncMock()
        listener = _linux.ServerMouseListener(
            event_bus, stream, command_stream, filtering=False
        )
        assert listener._barrier_mode, "fixture must exercise the barrier path"

        listener._active_clients = {CLIENT_UID: True}
        listener._active_clients_snapshot = (CLIENT_UID,)
        listener._edge_bindings_by_client = {CLIENT_UID: [_binding()]}
        listener._edge_bindings_snapshot = ((CLIENT_UID, (_binding(),)),)
        listener._listener = MagicMock()

        yield listener


@pytest.fixture
def event_bus():
    bus = AsyncMock()
    bus.dispatch = AsyncMock()
    bus.subscribe = MagicMock()
    return bus


class TestBarrierActivationPackets:
    """What the server sends a client when a barrier hands the cursor over."""

    @pytest.mark.anyio
    async def test_pushes_topology_before_cross_screen(self, wayland_listener):
        # The regression: no topology meant no return path at all.
        await wayland_listener._on_barrier_activated("right", SCREEN_W - 1, 540)

        sent = [c.args[0] for c in wayland_listener.command_stream.send.call_args_list]
        kinds = [type(e) for e in sent]
        assert ClientTopologyCommandEvent in kinds, (
            "without edge_bindings + server_bbox the client cannot resolve "
            "return-to-server and the cursor is stranded"
        )
        assert kinds.index(ClientTopologyCommandEvent) < kinds.index(
            CrossScreenCommandEvent
        ), "topology must arrive before the activation it applies to"

        topology = sent[kinds.index(ClientTopologyCommandEvent)]
        assert topology.target == CLIENT_UID
        assert topology.get_edge_bindings()
        assert topology.get_server_bbox() == (0, 0, SCREEN_W, SCREEN_H)

    @pytest.mark.anyio
    async def test_cross_screen_carries_entry_edge_and_monitor(self, wayland_listener):
        await wayland_listener._on_barrier_activated("right", SCREEN_W - 1, 540)

        sent = [c.args[0] for c in wayland_listener.command_stream.send.call_args_list]
        cross = next(e for e in sent if isinstance(e, CrossScreenCommandEvent))
        # Without an explicit entry_edge the client can't arm its return lock:
        # it infers the edge from the landing coords with eps=1e-3, which the
        # old 2% inset never matched.
        assert cross.get_entry_edge() == "left"
        assert cross.get_client_monitor_id() == 7

    @pytest.mark.anyio
    async def test_lands_exactly_on_the_entry_edge(self, wayland_listener):
        await wayland_listener._on_barrier_activated("right", SCREEN_W - 1, 540)

        position = wayland_listener.stream.send.await_args.args[0]
        assert position.action == MouseEvent.POSITION_ACTION
        # Crossing the server's right edge lands on the client's left (x=0),
        # not 2% inside it.
        assert position.x == 0
        assert position.y == pytest.approx(540 / SCREEN_H, abs=1e-3)

    @pytest.mark.anyio
    async def test_marks_the_client_active(self, wayland_listener):
        await wayland_listener._on_barrier_activated("right", SCREEN_W - 1, 540)
        assert wayland_listener._active_client_barrier == CLIENT_UID

    @pytest.mark.anyio
    async def test_ignores_activation_while_a_client_is_already_active(
        self, wayland_listener
    ):
        wayland_listener._active_client_barrier = CLIENT_UID
        await wayland_listener._on_barrier_activated("right", SCREEN_W - 1, 540)
        wayland_listener.command_stream.send.assert_not_awaited()


class TestPartialEdgeActivation:
    """An edge may be bound along only part of its length."""

    @pytest.mark.anyio
    async def test_crosses_inside_the_bound_span(self, wayland_listener):
        # Client occupies the top half of the right edge.
        binding = _binding(server_axis=(0.0, 0.5))
        wayland_listener._edge_bindings_by_client = {CLIENT_UID: [binding]}
        wayland_listener._edge_bindings_snapshot = ((CLIENT_UID, (binding,)),)

        await wayland_listener._on_barrier_activated("right", SCREEN_W - 1, 200)

        assert wayland_listener._active_client_barrier == CLIENT_UID

    @pytest.mark.anyio
    async def test_does_not_cross_outside_the_bound_span(self, wayland_listener):
        binding = _binding(server_axis=(0.0, 0.5))
        wayland_listener._edge_bindings_by_client = {CLIENT_UID: [binding]}
        wayland_listener._edge_bindings_snapshot = ((CLIENT_UID, (binding,)),)

        # Bottom half of the same edge: nothing is placed there.
        await wayland_listener._on_barrier_activated("right", SCREEN_W - 1, 900)

        assert wayland_listener._active_client_barrier is None
        wayland_listener.command_stream.send.assert_not_awaited()

    @pytest.mark.anyio
    async def test_releases_the_pointer_when_it_does_not_cross(self, wayland_listener):
        binding = _binding(server_axis=(0.0, 0.5))
        wayland_listener._edge_bindings_by_client = {CLIENT_UID: [binding]}
        wayland_listener._edge_bindings_snapshot = ((CLIENT_UID, (binding,)),)

        await wayland_listener._on_barrier_activated("right", SCREEN_W - 1, 900)

        # The compositor is holding the pointer at the barrier; not releasing
        # would strand it there.
        wayland_listener._listener.disable_capture.assert_called_once()


class TestBarrierSegments:
    """Which parts of which edges get an armed barrier."""

    def test_full_edge_binding_spans_the_whole_edge(self, wayland_listener):
        segments = wayland_listener._barrier_segments()
        assert segments == [("right", SCREEN_W, 0, SCREEN_W, SCREEN_H - 1)]

    def test_partial_binding_spans_only_its_portion(self, wayland_listener):
        binding = _binding(server_axis=(0.25, 0.75))
        wayland_listener._edge_bindings_by_client = {CLIENT_UID: [binding]}

        (edge, x1, y1, x2, y2) = wayland_listener._barrier_segments()[0]
        assert edge == "right"
        assert (x1, x2) == (SCREEN_W, SCREEN_W)
        assert (y1, y2) == (270, 809)

    def test_horizontal_edge_is_partitioned_along_x(self, wayland_listener):
        binding = _binding(server_edge="top", server_axis=(0.0, 0.5))
        wayland_listener._edge_bindings_by_client = {CLIENT_UID: [binding]}

        assert wayland_listener._barrier_segments() == [("top", 0, 0, 959, 0)]

    def test_disjoint_spans_on_one_edge_are_separate_segments(self, wayland_listener):
        # Two clients against the same edge with a gap between them: the gap
        # must stay barrier-free or the cursor stalls in it.
        wayland_listener._edge_bindings_by_client = {
            "a": [_binding(server_axis=(0.0, 0.25))],
            "b": [_binding(server_axis=(0.75, 1.0))],
        }

        segments = wayland_listener._barrier_segments()
        assert len(segments) == 2
        assert {s[0] for s in segments} == {"right"}
        assert sorted((s[2], s[4]) for s in segments) == [(0, 269), (810, 1079)]

    def test_no_bindings_means_no_segments_and_no_edges(self, wayland_listener):
        wayland_listener._edge_bindings_by_client = {}

        state = wayland_listener._refresh_edge_state()
        assert state["segments"] == []
        assert state["edges"] == {}, "the whole border must stay reachable"

    def test_zero_width_span_is_skipped(self, wayland_listener):
        wayland_listener._edge_bindings_by_client = {
            CLIENT_UID: [_binding(server_axis=(0.5, 0.5))]
        }
        assert wayland_listener._barrier_segments() == []

    def test_binding_for_an_unknown_monitor_is_skipped(self, wayland_listener):
        binding = _binding()
        binding["server_monitor_id"] = 99
        wayland_listener._edge_bindings_by_client = {CLIENT_UID: [binding]}
        assert wayland_listener._barrier_segments() == []

    def test_edge_state_reports_the_edges_covered_by_segments(self, wayland_listener):
        wayland_listener._edge_bindings_by_client = {
            CLIENT_UID: [_binding(), _binding(server_edge="top")]
        }
        state = wayland_listener._refresh_edge_state()
        assert state["edges"] == {"right": True, "top": True}
        assert len(state["segments"]) == 2


class TestBarrierRearming:
    """Every topology change must re-arm the barriers."""

    @pytest.mark.anyio
    async def test_layout_update_rearms(self, wayland_listener):
        with patch.object(
            type(wayland_listener).__mro__[1], "_on_client_layout_updated", AsyncMock()
        ):
            await wayland_listener._on_client_layout_updated(MagicMock())
        wayland_listener._listener.update_clients.assert_called_once()

    @pytest.mark.anyio
    async def test_server_monitor_hotplug_rearms(self, wayland_listener):
        # Segments are computed from the monitor layout, so stale ones would
        # sit at the old monitor's coordinates.
        with patch.object(
            type(wayland_listener).__mro__[1], "_on_local_monitors_updated", AsyncMock()
        ):
            await wayland_listener._on_local_monitors_updated(MagicMock())
        wayland_listener._listener.update_clients.assert_called_once()

    @pytest.mark.anyio
    async def test_rearm_payload_carries_edges_and_segments(self, wayland_listener):
        with patch.object(
            type(wayland_listener).__mro__[1], "_on_client_layout_updated", AsyncMock()
        ):
            await wayland_listener._on_client_layout_updated(MagicMock())

        state = wayland_listener._listener.update_clients.call_args.args[0]
        assert state["edges"] == {"right": True}
        assert state["segments"] == [("right", SCREEN_W, 0, SCREEN_W, SCREEN_H - 1)]


class TestServerControllerNoSecondWarp:
    """The portal owns the return landing on Wayland."""

    @pytest.mark.anyio
    async def test_barrier_mode_does_not_reposition(self, event_bus):
        from event import ActiveScreenChangedEvent

        with ExitStack() as stack:
            for p in _patch_wayland_geometry():
                stack.enter_context(p)
            from input.mouse import _linux

            controller = _linux.ServerMouseController(event_bus)
            # No controller is even built: writing a position would go through
            # libei's virtual accumulator, which nothing syncs to the real
            # pointer on a server.
            assert controller._controller is None

            controller.position_cursor = MagicMock()
            await controller._on_active_screen_changed(
                ActiveScreenChangedEvent(
                    active_screen=None, source="server", position=(0.5, 0.5)
                )
            )
            controller.position_cursor.assert_not_called()
