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

"""Unit tests for the monitor / layout model.

Covers the data shapes that feed into of the multi-monitor work:

- :class:`MonitorInfo` + :class:`MonitorLayout` (geometry helpers,
  neighbour detection in asymmetric arrangements).
- :class:`LayoutSlot` / :class:`LayoutBinding` (round-trip serialization,
  overlap semantics).
- :class:`LayoutValidator` (no-overlap invariant + slot routing).
"""

import pytest

from utils.screen import (
    Edge,
    EdgeBinding,
    LayoutBinding,
    LayoutReconciliation,
    LayoutSlot,
    LayoutValidator,
    MonitorInfo,
    MonitorLayout,
    compute_edge_bindings,
    reconcile_bindings_with_client_monitors,
)


def _mon(monitor_id: int, x: int, y: int, w: int, h: int, primary: bool = False) -> MonitorInfo:
    return MonitorInfo(
        monitor_id=monitor_id,
        min_x=x,
        min_y=y,
        max_x=x + w,
        max_y=y + h,
        is_primary=primary,
    )


class TestMonitorLayoutNeighbors:
    """Validate the asymmetric-layout bug fix:

    When a secondary monitor sits above a narrower primary AND extends
    past the primary's X range, the primary monitor's left/right edges
    are interior to the union bbox. ``has_neighbor_*`` must return
    False at the primary's outer X edges (no neighbour at the primary's
    Y range) so the cursor crossing fires correctly.
    """

    def test_asymmetric_layout_primary_has_no_horizontal_neighbour(self):
        # Upper monitor (wider) placed above the primary (narrower).
        upper = _mon(monitor_id=1, x=-300, y=-1080, w=2520, h=1080)
        primary = _mon(monitor_id=0, x=0, y=0, w=1920, h=1080, primary=True)
        layout = MonitorLayout(monitors=(primary, upper))

        # On the primary (y in [0, 1080)) the left and right edges of
        # the primary are outer edges: no neighbour at that Y.
        assert layout.has_neighbor_left(primary, y=500) is False
        assert layout.has_neighbor_right(primary, y=500) is False
        # The primary's top edge however IS bordered by the upper monitor.
        assert layout.has_neighbor_top(primary, x=960) is True

    def test_side_by_side_monitors_have_horizontal_neighbours(self):
        left = _mon(monitor_id=0, x=0, y=0, w=1920, h=1080, primary=True)
        right = _mon(monitor_id=1, x=1920, y=0, w=1920, h=1080)
        layout = MonitorLayout(monitors=(left, right))

        # Inner edges have neighbours; outer edges don't.
        assert layout.has_neighbor_right(left, y=500) is True
        assert layout.has_neighbor_left(right, y=500) is True
        assert layout.has_neighbor_left(left, y=500) is False
        assert layout.has_neighbor_right(right, y=500) is False

    def test_neighbour_check_respects_y_range(self):
        # Top monitor only covers part of the primary's X range; cursor
        # below it should still report "no top neighbour" for the
        # primary because the top monitor doesn't extend to that X.
        primary = _mon(monitor_id=0, x=0, y=0, w=1920, h=1080, primary=True)
        partial_top = _mon(monitor_id=1, x=0, y=-720, w=600, h=720)
        layout = MonitorLayout(monitors=(primary, partial_top))

        assert layout.has_neighbor_top(primary, x=300) is True
        # Past the partial top monitor's right edge: no neighbour.
        assert layout.has_neighbor_top(primary, x=1500) is False


class TestLayoutSlot:
    def test_segment_validation_rejects_inverted_range(self):
        with pytest.raises(ValueError):
            LayoutSlot(monitor_id=0, edge=Edge.RIGHT, segment_start=0.6, segment_end=0.5)

    def test_segment_validation_rejects_empty_range(self):
        with pytest.raises(ValueError):
            LayoutSlot(monitor_id=0, edge=Edge.RIGHT, segment_start=0.5, segment_end=0.5)

    def test_segment_validation_rejects_out_of_unit_range(self):
        with pytest.raises(ValueError):
            LayoutSlot(monitor_id=0, edge=Edge.RIGHT, segment_start=-0.1, segment_end=0.5)
        with pytest.raises(ValueError):
            LayoutSlot(monitor_id=0, edge=Edge.RIGHT, segment_start=0.0, segment_end=1.5)

    def test_is_full_edge(self):
        assert LayoutSlot(0, Edge.RIGHT).is_full_edge() is True
        assert LayoutSlot(0, Edge.RIGHT, 0.0, 0.5).is_full_edge() is False

    def test_overlaps_disjoint_when_different_monitor(self):
        a = LayoutSlot(0, Edge.RIGHT)
        b = LayoutSlot(1, Edge.RIGHT)
        assert a.overlaps(b) is False

    def test_overlaps_disjoint_when_different_edge(self):
        a = LayoutSlot(0, Edge.RIGHT)
        b = LayoutSlot(0, Edge.LEFT)
        assert a.overlaps(b) is False

    def test_overlaps_clean_split_is_disjoint(self):
        # Touching at a single point counts as disjoint so 0/0.5/1 split works.
        top = LayoutSlot(0, Edge.RIGHT, 0.0, 0.5)
        bottom = LayoutSlot(0, Edge.RIGHT, 0.5, 1.0)
        assert top.overlaps(bottom) is False
        assert bottom.overlaps(top) is False

    def test_overlaps_when_segments_intersect(self):
        a = LayoutSlot(0, Edge.RIGHT, 0.0, 0.6)
        b = LayoutSlot(0, Edge.RIGHT, 0.4, 1.0)
        assert a.overlaps(b) is True
        assert b.overlaps(a) is True

    def test_contains_secondary_half_open_interval(self):
        slot = LayoutSlot(0, Edge.RIGHT, 0.0, 0.5)
        assert slot.contains_secondary(0.0) is True
        assert slot.contains_secondary(0.49) is True
        # Upper bound is exclusive so split boundaries route deterministically.
        assert slot.contains_secondary(0.5) is False

    def test_roundtrip_dict(self):
        slot = LayoutSlot(7, Edge.TOP, 0.25, 0.75)
        assert LayoutSlot.from_dict(slot.to_dict()) == slot


class TestLayoutBinding:
    def test_roundtrip_dict(self):
        slot = LayoutSlot(3, Edge.LEFT, 0.0, 0.5)
        b = LayoutBinding(slot=slot, client_uid="client-A", client_monitor_id=2)
        assert LayoutBinding.from_dict(b.to_dict()) == b

    def test_roundtrip_dict_without_target_monitor(self):
        slot = LayoutSlot(0, Edge.RIGHT)
        b = LayoutBinding(slot=slot, client_uid="client-B")
        restored = LayoutBinding.from_dict(b.to_dict())
        assert restored == b
        assert restored.client_monitor_id is None


class TestLayoutValidator:
    def test_disjoint_layout_validates(self):
        bindings = [
            LayoutBinding(LayoutSlot(0, Edge.RIGHT, 0.0, 0.5), "client-A"),
            LayoutBinding(LayoutSlot(0, Edge.RIGHT, 0.5, 1.0), "client-B"),
            LayoutBinding(LayoutSlot(1, Edge.LEFT), "client-C"),
        ]
        ok, errors = LayoutValidator().validate(bindings)
        assert ok is True
        assert errors == []

    def test_overlap_is_rejected_with_descriptive_error(self):
        bindings = [
            LayoutBinding(LayoutSlot(0, Edge.RIGHT, 0.0, 0.6), "client-A"),
            LayoutBinding(LayoutSlot(0, Edge.RIGHT, 0.4, 1.0), "client-B"),
        ]
        ok, errors = LayoutValidator().validate(bindings)
        assert ok is False
        assert len(errors) == 1
        # Both client UIDs surface so a GUI can highlight both rows.
        assert "client-A" in errors[0]
        assert "client-B" in errors[0]

    def test_multiple_overlaps_all_reported(self):
        # Three slots all overlapping on the same edge.
        bindings = [
            LayoutBinding(LayoutSlot(0, Edge.RIGHT, 0.0, 0.8), "client-A"),
            LayoutBinding(LayoutSlot(0, Edge.RIGHT, 0.2, 1.0), "client-B"),
            LayoutBinding(LayoutSlot(0, Edge.RIGHT, 0.3, 0.7), "client-C"),
        ]
        ok, errors = LayoutValidator().validate(bindings)
        assert ok is False
        # Three pairwise conflicts: (A,B), (A,C), (B,C).
        assert len(errors) == 3

    def test_unknown_monitor_id_reported(self):
        bindings = [LayoutBinding(LayoutSlot(99, Edge.RIGHT), "client-A")]
        ok, errors = LayoutValidator(known_monitor_ids={0, 1}).validate(bindings)
        assert ok is False
        assert any("99" in e for e in errors)

    def test_slot_for_routes_to_matching_binding(self):
        bindings = [
            LayoutBinding(LayoutSlot(0, Edge.RIGHT, 0.0, 0.5), "client-A"),
            LayoutBinding(LayoutSlot(0, Edge.RIGHT, 0.5, 1.0), "client-B"),
        ]
        v = LayoutValidator()
        a = v.slot_for(bindings, monitor_id=0, edge=Edge.RIGHT, axis_norm=0.2)
        b = v.slot_for(bindings, monitor_id=0, edge=Edge.RIGHT, axis_norm=0.8)
        assert a is not None and a.client_uid == "client-A"
        assert b is not None and b.client_uid == "client-B"

    def test_slot_for_returns_none_when_segment_unassigned(self):
        bindings = [
            LayoutBinding(LayoutSlot(0, Edge.RIGHT, 0.0, 0.5), "client-A"),
        ]
        v = LayoutValidator()
        assert v.slot_for(bindings, 0, Edge.RIGHT, 0.7) is None
        assert v.slot_for(bindings, 0, Edge.LEFT, 0.2) is None
        assert v.slot_for(bindings, 1, Edge.RIGHT, 0.2) is None


class TestComplexLayouts:
    """End-to-end checks on richer arrangements: T-shape, L-shape, four
    monitors, mixed scaling, etc. These exercise neighbour detection +
    edge routing in the configurations real users will hit."""

    def test_t_shape_layout_outer_edges(self):
        # Three monitors in a T: top center on top of two side-by-side
        # primary + right monitors.
        #
        #          [-- top (id=2) --]
        #   [--- primary (id=0) ---][--- right (id=1) ---]
        #
        # Outer edges (no neighbour at the orthogonal coord):
        #   primary.left, primary.bottom, primary.top (only where top doesn't cover)
        #   right.right, right.bottom, right.top (only where top doesn't cover)
        #   top.top, top.left, top.right
        primary = _mon(0, 0, 0, 1920, 1080, primary=True)
        right = _mon(1, 1920, 0, 1920, 1080)
        top = _mon(2, 960, -1080, 1920, 1080)
        layout = MonitorLayout(monitors=(primary, right, top))

        # Primary: bottom is outer everywhere.
        assert layout.has_neighbor_bottom(primary, x=100) is False
        assert layout.has_neighbor_bottom(primary, x=1800) is False
        # Primary: top is covered by `top` only for x in [960, 1920);
        # below 960 it's an outer top edge.
        assert layout.has_neighbor_top(primary, x=500) is False
        assert layout.has_neighbor_top(primary, x=1500) is True
        # Primary: left is outer; right has the `right` monitor as neighbour.
        assert layout.has_neighbor_left(primary, y=500) is False
        assert layout.has_neighbor_right(primary, y=500) is True

        # Top monitor: top/left/right are all outer.
        assert layout.has_neighbor_top(top, x=1500) is False
        assert layout.has_neighbor_left(top, y=-500) is False
        assert layout.has_neighbor_right(top, y=-500) is False
        # Top monitor's bottom is partially bordered (primary covers
        # x in [960, 1920), right covers x in [1920, 2880)).
        assert layout.has_neighbor_bottom(top, x=1500) is True
        assert layout.has_neighbor_bottom(top, x=2000) is True

    def test_l_shape_layout_has_dead_zone_handled(self):
        # L-shape: primary at origin, smaller monitor only on top-left.
        #
        #   [-- top-left --]
        #   [---- primary ----]
        #
        # The "dead zone" is the top-right area (x in [1280, 1920),
        # y < 0) — no monitor there. The primary's top is partially
        # outer.
        primary = _mon(0, 0, 0, 1920, 1080, primary=True)
        top_left = _mon(1, 0, -720, 1280, 720)
        layout = MonitorLayout(monitors=(primary, top_left))

        # Primary's top is bordered by top_left only on the left half.
        assert layout.has_neighbor_top(primary, x=500) is True
        assert layout.has_neighbor_top(primary, x=1500) is False

        # find_monitor_at in the dead zone returns None; the edge
        # detector then snaps to the closest monitor.
        assert layout.find_monitor_at(1500, -300) is None
        # Primary still contains its own pixels.
        assert layout.find_monitor_at(960, 500) is primary

    def test_four_monitor_2x2_grid(self):
        # 2x2 grid, all 1920x1080. Each monitor has 2 inner edges (with
        # neighbours) and 2 outer edges.
        tl = _mon(0, 0, 0, 1920, 1080, primary=True)
        tr = _mon(1, 1920, 0, 1920, 1080)
        bl = _mon(2, 0, 1080, 1920, 1080)
        br = _mon(3, 1920, 1080, 1920, 1080)
        layout = MonitorLayout(monitors=(tl, tr, bl, br))

        # Top-left: top + left outer, right + bottom have neighbours.
        assert layout.has_neighbor_top(tl, x=500) is False
        assert layout.has_neighbor_left(tl, y=500) is False
        assert layout.has_neighbor_right(tl, y=500) is True
        assert layout.has_neighbor_bottom(tl, x=500) is True

        # Bottom-right: bottom + right outer, top + left have neighbours.
        assert layout.has_neighbor_bottom(br, x=2400) is False
        assert layout.has_neighbor_right(br, y=1500) is False
        assert layout.has_neighbor_top(br, x=2400) is True
        assert layout.has_neighbor_left(br, y=1500) is True

    def test_complex_layout_full_slot_routing(self):
        # Three monitors, four clients: top-half right edge of monitor 0
        # to client-A, bottom-half right edge to client-B, monitor 1's
        # right edge to client-C, monitor 2 (above) top edge to client-D.
        bindings = [
            LayoutBinding(LayoutSlot(0, Edge.RIGHT, 0.0, 0.5), "client-A"),
            LayoutBinding(LayoutSlot(0, Edge.RIGHT, 0.5, 1.0), "client-B"),
            LayoutBinding(LayoutSlot(1, Edge.RIGHT), "client-C"),
            LayoutBinding(LayoutSlot(2, Edge.TOP), "client-D"),
        ]

        v = LayoutValidator(known_monitor_ids={0, 1, 2})
        ok, errors = v.validate(bindings)
        assert ok, errors

        def _route(monitor_id: int, edge: Edge, axis_norm: float) -> str:
            b = v.slot_for(bindings, monitor_id, edge, axis_norm)
            assert b is not None, (
                f"no slot for ({monitor_id}, {edge}, {axis_norm})"
            )
            return b.client_uid

        # Top half of m0 right -> client-A
        assert _route(0, Edge.RIGHT, 0.1) == "client-A"
        # Bottom half of m0 right -> client-B
        assert _route(0, Edge.RIGHT, 0.9) == "client-B"
        # m1 right is one whole slot -> client-C
        assert _route(1, Edge.RIGHT, 0.3) == "client-C"
        # m2 top -> client-D
        assert _route(2, Edge.TOP, 0.5) == "client-D"
        # m1 left has no binding -> None
        assert v.slot_for(bindings, 1, Edge.LEFT, 0.5) is None


class TestLayoutReconciliation:
    """Resiliency contract for the reconnection path: when a client
    advertises a different monitor list than the one the layout was
    configured against, bindings that pinned the client to a missing
    monitor must be surfaced without crashing the routing layer."""

    def test_client_disconnects_monitor_drops_pinned_binding(self):
        bindings = [
            # Pinned binding: client A's monitor #2 is the target.
            LayoutBinding(
                LayoutSlot(0, Edge.RIGHT, 0.0, 0.5),
                "client-A",
                client_monitor_id=2,
            ),
            # Unpinned (any monitor) binding for same client — survives.
            LayoutBinding(
                LayoutSlot(0, Edge.RIGHT, 0.5, 1.0),
                "client-A",
            ),
        ]
        # Client reconnects with only monitor 0 and 1 (the #2 is gone).
        result = reconcile_bindings_with_client_monitors(
            bindings, client_uid="client-A", client_monitor_ids=[0, 1]
        )

        assert isinstance(result, LayoutReconciliation)
        assert not result.is_clean
        assert len(result.dropped) == 1
        assert result.dropped[0].client_monitor_id == 2
        assert len(result.kept) == 1
        assert result.kept[0].client_monitor_id is None
        assert result.missing_monitor_ids == frozenset({2})

    def test_unrelated_client_bindings_are_kept(self):
        # Bindings for OTHER clients must not be touched by a single
        # client's reconnection.
        bindings = [
            LayoutBinding(LayoutSlot(0, Edge.RIGHT), "client-A", client_monitor_id=5),
            LayoutBinding(LayoutSlot(0, Edge.LEFT), "client-B", client_monitor_id=99),
        ]
        result = reconcile_bindings_with_client_monitors(
            bindings, client_uid="client-A", client_monitor_ids=[0]
        )

        # Client-A's pinned binding is dropped; client-B's is kept.
        assert len(result.dropped) == 1
        assert result.dropped[0].client_uid == "client-A"
        kept_uids = {b.client_uid for b in result.kept}
        assert "client-B" in kept_uids

    def test_all_monitors_present_keeps_everything(self):
        bindings = [
            LayoutBinding(LayoutSlot(0, Edge.RIGHT), "client-A", client_monitor_id=0),
            LayoutBinding(LayoutSlot(0, Edge.LEFT), "client-A", client_monitor_id=1),
        ]
        result = reconcile_bindings_with_client_monitors(
            bindings, client_uid="client-A", client_monitor_ids=[0, 1]
        )
        assert result.is_clean
        assert len(result.kept) == 2
        assert result.dropped == ()

    def test_client_with_no_monitors_drops_all_pinned(self):
        # Edge case: client reports empty monitor list (legacy / probe).
        # Every pinned binding for that client gets dropped.
        bindings = [
            LayoutBinding(LayoutSlot(0, Edge.RIGHT), "client-A", client_monitor_id=0),
            LayoutBinding(LayoutSlot(0, Edge.LEFT), "client-A"),
        ]
        result = reconcile_bindings_with_client_monitors(
            bindings, client_uid="client-A", client_monitor_ids=[]
        )
        assert len(result.dropped) == 1
        assert len(result.kept) == 1  # the unpinned one survives


class TestComputeEdgeBindings:
    """The unified runtime contract derived from a client's placements
    + server's monitor list.

    Each placement that abuts a server monitor on at least one side
    yields one :class:`EdgeBinding` per touched edge. The
    ``server_axis_*`` range is normalised over the server monitor's
    edge length (used by the server listener for forward routing);
    ``client_axis_*`` is the mirror over the client monitor's edge
    length (used by the client controller for return-to-server
    crossings). A single binding therefore drives both directions.
    """

    @staticmethod
    def _placement(client_monitor_id: int, x: int, y: int, w: int, h: int) -> dict:
        return {
            "client_monitor_id": client_monitor_id,
            "workspace_x": x,
            "workspace_y": y,
            "width": w,
            "height": h,
        }

    def test_full_right_abutment_produces_full_segment(self):
        # Server monitor at origin 1920x1080; client monitor flush to
        # the right, same height -> full [0, 1] segment on both sides.
        server = _mon(0, 0, 0, 1920, 1080, primary=True)
        placement = self._placement(0, 1920, 0, 1280, 1080)
        out = compute_edge_bindings(placement, [server])
        assert len(out) == 1
        b = out[0]
        assert isinstance(b, EdgeBinding)
        assert b.server_monitor_id == 0
        assert b.server_edge == Edge.RIGHT
        assert b.server_axis_start == 0.0
        assert b.server_axis_end == 1.0
        assert b.client_monitor_id == 0
        assert b.client_edge == Edge.LEFT
        assert b.client_axis_start == 0.0
        assert b.client_axis_end == 1.0
        # Server monitor bounds embedded for client-side absolute
        # positioning.
        assert (b.server_monitor_min_x, b.server_monitor_min_y) == (0, 0)
        assert (b.server_monitor_max_x, b.server_monitor_max_y) == (1920, 1080)

    def test_partial_right_abutment_produces_partial_segment(self):
        # Client monitor smaller than server, vertically centered:
        # Y range [200, 920) of server's 1080 -> normalized
        # ~[0.185, 0.852); the same overlap on the client (height 720)
        # covers the whole client edge (0..720 / 720).
        server = _mon(0, 0, 0, 1920, 1080)
        placement = self._placement(0, 1920, 200, 1280, 720)
        out = compute_edge_bindings(placement, [server])
        assert len(out) == 1
        b = out[0]
        assert b.server_edge == Edge.RIGHT
        assert b.client_edge == Edge.LEFT
        assert b.server_axis_start == pytest.approx(200 / 1080)
        assert b.server_axis_end == pytest.approx(920 / 1080)
        assert b.client_axis_start == pytest.approx(0.0)
        assert b.client_axis_end == pytest.approx(1.0)

    def test_top_abutment_uses_x_axis(self):
        # Client monitor sitting on top of the server: TOP edge,
        # axis range normalized over the server's WIDTH.
        server = _mon(0, 0, 0, 1920, 1080)
        # Client 1280x720 placed flush above the server at x=320.
        placement = self._placement(0, 320, -720, 1280, 720)
        out = compute_edge_bindings(placement, [server])
        assert len(out) == 1
        b = out[0]
        assert b.server_edge == Edge.TOP
        assert b.client_edge == Edge.BOTTOM
        assert b.server_axis_start == pytest.approx(320 / 1920)
        assert b.server_axis_end == pytest.approx((320 + 1280) / 1920)
        assert b.client_axis_start == pytest.approx(0.0)
        assert b.client_axis_end == pytest.approx(1.0)

    def test_no_binding_when_separated_by_gap(self):
        # 100 px gap on the right -> no abutment.
        server = _mon(0, 0, 0, 1920, 1080)
        placement = self._placement(0, 2020, 0, 1280, 1080)
        assert compute_edge_bindings(placement, [server]) == []

    def test_pixel_tolerance_allows_one_pixel_gap(self):
        # 1-pixel gap is still considered an abutment so GUI rounding
        # doesn't silently disconnect adjacent boxes.
        server = _mon(0, 0, 0, 1920, 1080)
        placement = self._placement(0, 1921, 0, 1280, 1080)
        out = compute_edge_bindings(placement, [server])
        assert len(out) == 1
        assert out[0].server_edge == Edge.RIGHT

    def test_corner_straddle_produces_two_bindings(self):
        # Client monitor wraps the right+bottom corner of a server
        # monitor: it abuts on RIGHT and on TOP of the lower-right
        # server monitor at the same time.
        upper = _mon(0, 0, 0, 1920, 1080)
        lower_right = _mon(1, 1920, 1080, 1920, 1080)
        # Placement sits at (1920, 1080) flush against both.
        placement = self._placement(0, 1920, 540, 1280, 540)
        out = compute_edge_bindings(placement, [upper, lower_right])
        # Two bindings: right of `upper` and top of `lower_right`.
        edges = sorted((b.server_monitor_id, b.server_edge.value) for b in out)
        assert (0, "right") in edges
        assert (1, "top") in edges

    def test_invalid_zero_size_placement_returns_empty(self):
        server = _mon(0, 0, 0, 1920, 1080)
        placement = self._placement(0, 1920, 0, 0, 0)
        assert compute_edge_bindings(placement, [server]) == []

    def test_edge_binding_contains_server_axis_half_open(self):
        # Half-open server_axis range so split boundaries route
        # deterministically.
        b = compute_edge_bindings(
            self._placement(0, 1920, 0, 1280, 540),
            [_mon(0, 0, 0, 1920, 1080)],
        )[0]
        assert b.contains_server_axis(b.server_axis_start) is True
        assert b.contains_server_axis(b.server_axis_end) is False

    def test_edge_binding_axis_mapping_inverts(self):
        # The forward/reverse axis maps are exact inverses by
        # construction — round-trip a few sample points.
        b = compute_edge_bindings(
            self._placement(0, 1920, 200, 1280, 720),
            [_mon(0, 0, 0, 1920, 1080)],
        )[0]
        for s in (b.server_axis_start, 0.5 * (b.server_axis_start + b.server_axis_end)):
            mapped = b.map_server_to_client_axis(s)
            assert b.map_client_to_server_axis(mapped) == pytest.approx(s)

    def test_edge_binding_roundtrip_dict(self):
        b = compute_edge_bindings(
            self._placement(1, 1920, 0, 1280, 1080),
            [_mon(3, 0, 0, 1920, 1080)],
        )[0]
        d = b.to_dict()
        # Keys present on both sides — the client reads ``client_*``,
        # the server reads ``server_*``, both off the same dict.
        for k in (
            "server_monitor_id", "server_edge",
            "server_axis_start", "server_axis_end",
            "server_monitor_min_x", "server_monitor_min_y",
            "server_monitor_max_x", "server_monitor_max_y",
            "client_monitor_id", "client_edge",
            "client_axis_start", "client_axis_end",
        ):
            assert k in d
        assert d["server_edge"] == "right"
        assert d["client_edge"] == "left"


class TestClientObjPlacements:
    """``ClientObj.placements`` round-trip + ``get_edge_bindings``
    bridge into the runtime data model."""

    def _make_client(self, placements):
        from model.client import ClientObj
        return ClientObj(
            uid="client-A",
            ip_addresses=["10.0.0.1"],
            hostname="client-a.local",
            placements=placements,
        )

    def test_placements_roundtrip_through_dict(self):
        placements = [
            {
                "client_monitor_id": 0,
                "workspace_x": 1920,
                "workspace_y": 0,
                "width": 1280,
                "height": 1080,
            }
        ]
        c = self._make_client(placements)
        from model.client import ClientObj
        restored = ClientObj.from_dict(c.to_dict())
        assert restored.placements == placements

    def test_get_edge_bindings_uses_server_monitors(self):
        placements = [
            {
                "client_monitor_id": 0,
                "workspace_x": 1920,
                "workspace_y": 200,
                "width": 1280,
                "height": 720,
            }
        ]
        c = self._make_client(placements)
        server_monitors = [_mon(0, 0, 0, 1920, 1080)]
        bindings = c.get_edge_bindings(server_monitors)
        assert len(bindings) == 1
        assert bindings[0].server_monitor_id == 0
        assert bindings[0].server_edge == Edge.RIGHT
        assert bindings[0].client_monitor_id == 0

    def test_get_edge_bindings_empty_when_no_placements(self):
        # Default screen_position is CENTER → no synthetic placement,
        # no bindings. Pre-layout clients with a legacy direction get a
        # synthetic placement instead (see the tests below).
        c = self._make_client([])
        server_monitors = [_mon(0, 0, 0, 1920, 1080)]
        assert c.get_edge_bindings(server_monitors) == []


class TestEffectivePlacementSynthesis:
    """Pre-layout clients should still cross-screen via the unified
    bindings: ``get_effective_placements`` synthesizes a 1:1 placement
    next to the server's primary monitor on the side indicated by the
    legacy ``screen_position``. This replaces the old parallel
    ScreenPosition routing path on the mouse listener.
    """

    def _make_client(self, screen_position, monitors=None):
        from model.client import ClientObj
        return ClientObj(
            uid="client-X",
            ip_addresses=["10.0.0.2"],
            hostname="client-x.local",
            screen_position=screen_position,
            monitors=monitors or [],
        )

    def test_center_returns_empty(self):
        c = self._make_client("center")
        assert c.get_effective_placements([_mon(0, 0, 0, 1920, 1080)]) == []

    def test_right_synthesizes_flush_right_placement(self):
        server = _mon(0, 0, 0, 1920, 1080, primary=True)
        c = self._make_client("right")
        ps = c.get_effective_placements([server])
        assert len(ps) == 1
        # Flush against the server's right edge at the same Y origin.
        assert ps[0]["workspace_x"] == 1920
        assert ps[0]["workspace_y"] == 0
        # The resulting binding covers the server's RIGHT edge fully.
        bindings = c.get_edge_bindings([server])
        assert len(bindings) == 1
        assert bindings[0].server_edge == Edge.RIGHT
        assert bindings[0].server_axis_start == 0.0
        assert bindings[0].server_axis_end == 1.0

    def test_left_synthesizes_above_server_origin(self):
        server = _mon(0, 0, 0, 1920, 1080, primary=True)
        c = self._make_client("left")
        ps = c.get_effective_placements([server])
        assert ps[0]["workspace_x"] == -ps[0]["width"]
        assert ps[0]["workspace_y"] == 0

    def test_top_synthesizes_above_server(self):
        server = _mon(0, 0, 0, 1920, 1080, primary=True)
        c = self._make_client("top")
        ps = c.get_effective_placements([server])
        assert ps[0]["workspace_x"] == 0
        assert ps[0]["workspace_y"] == -ps[0]["height"]

    def test_bottom_synthesizes_below_server(self):
        server = _mon(0, 0, 0, 1920, 1080, primary=True)
        c = self._make_client("bottom")
        ps = c.get_effective_placements([server])
        assert ps[0]["workspace_x"] == 0
        assert ps[0]["workspace_y"] == 1080

    def test_synthesis_uses_client_primary_monitor_dims(self):
        # When the client advertises monitor info we mirror its
        # primary's dimensions so denormalisation stays accurate.
        server = _mon(0, 0, 0, 1920, 1080, primary=True)
        client_mon = _mon(7, 0, 0, 1280, 720, primary=True)
        c = self._make_client("right", monitors=[client_mon])
        ps = c.get_effective_placements([server])
        assert ps[0]["client_monitor_id"] == 7
        assert ps[0]["width"] == 1280
        assert ps[0]["height"] == 720

    def test_real_placements_take_precedence_over_screen_position(self):
        # If both placements AND screen_position are set, placements win:
        # the editor's explicit layout is the source of truth.
        server = _mon(0, 0, 0, 1920, 1080, primary=True)
        c = self._make_client("right")
        c.placements = [{
            "client_monitor_id": 0,
            "workspace_x": 0,
            "workspace_y": -720,
            "width": 1280,
            "height": 720,
        }]
        ps = c.get_effective_placements([server])
        assert ps == c.placements
