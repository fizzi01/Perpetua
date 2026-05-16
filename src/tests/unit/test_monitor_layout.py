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

Covers the data shapes that feed into Phase 2 of the multi-monitor work:

- :class:`MonitorInfo` + :class:`MonitorLayout` (geometry helpers,
  neighbour detection in asymmetric arrangements).
- :class:`LayoutSlot` / :class:`LayoutBinding` (round-trip serialization,
  overlap semantics).
- :class:`LayoutValidator` (no-overlap invariant + slot routing).
"""

import pytest

from utils.screen import (
    Edge,
    LayoutBinding,
    LayoutReconciliation,
    LayoutSlot,
    LayoutValidator,
    MonitorInfo,
    MonitorLayout,
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
    """Validate the asymmetric-layout bug fix that motivated Phase 2:

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
