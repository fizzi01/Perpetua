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

"""Unit tests for the monitor / layout model."""

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
    compute_intra_client_bindings,
    reconcile_bindings_with_client_monitors,
)


def _mon(
    monitor_id: int, x: int, y: int, w: int, h: int, primary: bool = False
) -> MonitorInfo:
    return MonitorInfo(
        monitor_id=monitor_id,
        min_x=x,
        min_y=y,
        max_x=x + w,
        max_y=y + h,
        is_primary=primary,
    )


class TestMonitorLayoutNeighbors:
    def test_asymmetric_layout_primary_has_no_horizontal_neighbour(self):
        # Wider monitor above a narrower primary: primary's left/right
        # are outer at the primary's Y range.
        upper = _mon(monitor_id=1, x=-300, y=-1080, w=2520, h=1080)
        primary = _mon(monitor_id=0, x=0, y=0, w=1920, h=1080, primary=True)
        layout = MonitorLayout(monitors=(primary, upper))

        assert layout.has_neighbor_left(primary, y=500) is False
        assert layout.has_neighbor_right(primary, y=500) is False
        assert layout.has_neighbor_top(primary, x=960) is True

    def test_side_by_side_monitors_have_horizontal_neighbours(self):
        left = _mon(monitor_id=0, x=0, y=0, w=1920, h=1080, primary=True)
        right = _mon(monitor_id=1, x=1920, y=0, w=1920, h=1080)
        layout = MonitorLayout(monitors=(left, right))

        assert layout.has_neighbor_right(left, y=500) is True
        assert layout.has_neighbor_left(right, y=500) is True
        assert layout.has_neighbor_left(left, y=500) is False
        assert layout.has_neighbor_right(right, y=500) is False

    def test_neighbour_check_respects_y_range(self):
        primary = _mon(monitor_id=0, x=0, y=0, w=1920, h=1080, primary=True)
        partial_top = _mon(monitor_id=1, x=0, y=-720, w=600, h=720)
        layout = MonitorLayout(monitors=(primary, partial_top))

        assert layout.has_neighbor_top(primary, x=300) is True
        # Past the partial top monitor's right edge.
        assert layout.has_neighbor_top(primary, x=1500) is False


class TestLayoutSlot:
    def test_segment_validation_rejects_inverted_range(self):
        with pytest.raises(ValueError):
            LayoutSlot(
                monitor_id=0, edge=Edge.RIGHT, segment_start=0.6, segment_end=0.5
            )

    def test_segment_validation_rejects_empty_range(self):
        with pytest.raises(ValueError):
            LayoutSlot(
                monitor_id=0, edge=Edge.RIGHT, segment_start=0.5, segment_end=0.5
            )

    def test_segment_validation_rejects_out_of_unit_range(self):
        with pytest.raises(ValueError):
            LayoutSlot(
                monitor_id=0, edge=Edge.RIGHT, segment_start=-0.1, segment_end=0.5
            )
        with pytest.raises(ValueError):
            LayoutSlot(
                monitor_id=0, edge=Edge.RIGHT, segment_start=0.0, segment_end=1.5
            )

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
        # Touching at a single point is disjoint so a 0/0.5/1 split works.
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
        assert "client-A" in errors[0]
        assert "client-B" in errors[0]

    def test_multiple_overlaps_all_reported(self):
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
    def test_t_shape_layout_outer_edges(self):
        # T: top center over primary + right side-by-side.
        primary = _mon(0, 0, 0, 1920, 1080, primary=True)
        right = _mon(1, 1920, 0, 1920, 1080)
        top = _mon(2, 960, -1080, 1920, 1080)
        layout = MonitorLayout(monitors=(primary, right, top))

        assert layout.has_neighbor_bottom(primary, x=100) is False
        assert layout.has_neighbor_bottom(primary, x=1800) is False
        # top covers primary.top only for x in [960, 1920).
        assert layout.has_neighbor_top(primary, x=500) is False
        assert layout.has_neighbor_top(primary, x=1500) is True
        assert layout.has_neighbor_left(primary, y=500) is False
        assert layout.has_neighbor_right(primary, y=500) is True

        assert layout.has_neighbor_top(top, x=1500) is False
        assert layout.has_neighbor_left(top, y=-500) is False
        assert layout.has_neighbor_right(top, y=-500) is False
        assert layout.has_neighbor_bottom(top, x=1500) is True
        assert layout.has_neighbor_bottom(top, x=2000) is True

    def test_l_shape_layout_has_dead_zone_handled(self):
        # L-shape: primary + smaller monitor on top-left only. Dead zone
        # is top-right (no monitor there).
        primary = _mon(0, 0, 0, 1920, 1080, primary=True)
        top_left = _mon(1, 0, -720, 1280, 720)
        layout = MonitorLayout(monitors=(primary, top_left))

        assert layout.has_neighbor_top(primary, x=500) is True
        assert layout.has_neighbor_top(primary, x=1500) is False

        # Dead zone returns None; edge detector snaps to the closest monitor.
        assert layout.find_monitor_at(1500, -300) is None
        assert layout.find_monitor_at(960, 500) is primary

    def test_four_monitor_2x2_grid(self):
        tl = _mon(0, 0, 0, 1920, 1080, primary=True)
        tr = _mon(1, 1920, 0, 1920, 1080)
        bl = _mon(2, 0, 1080, 1920, 1080)
        br = _mon(3, 1920, 1080, 1920, 1080)
        layout = MonitorLayout(monitors=(tl, tr, bl, br))

        assert layout.has_neighbor_top(tl, x=500) is False
        assert layout.has_neighbor_left(tl, y=500) is False
        assert layout.has_neighbor_right(tl, y=500) is True
        assert layout.has_neighbor_bottom(tl, x=500) is True

        assert layout.has_neighbor_bottom(br, x=2400) is False
        assert layout.has_neighbor_right(br, y=1500) is False
        assert layout.has_neighbor_top(br, x=2400) is True
        assert layout.has_neighbor_left(br, y=1500) is True

    def test_complex_layout_full_slot_routing(self):
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
            assert b is not None, f"no slot for ({monitor_id}, {edge}, {axis_norm})"
            return b.client_uid

        assert _route(0, Edge.RIGHT, 0.1) == "client-A"
        assert _route(0, Edge.RIGHT, 0.9) == "client-B"
        assert _route(1, Edge.RIGHT, 0.3) == "client-C"
        assert _route(2, Edge.TOP, 0.5) == "client-D"
        assert v.slot_for(bindings, 1, Edge.LEFT, 0.5) is None


class TestLayoutReconciliation:
    def test_client_disconnects_monitor_drops_pinned_binding(self):
        bindings = [
            LayoutBinding(
                LayoutSlot(0, Edge.RIGHT, 0.0, 0.5),
                "client-A",
                client_monitor_id=2,
            ),
            # Unpinned binding for the same client must survive.
            LayoutBinding(
                LayoutSlot(0, Edge.RIGHT, 0.5, 1.0),
                "client-A",
            ),
        ]
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
        bindings = [
            LayoutBinding(LayoutSlot(0, Edge.RIGHT), "client-A", client_monitor_id=5),
            LayoutBinding(LayoutSlot(0, Edge.LEFT), "client-B", client_monitor_id=99),
        ]
        result = reconcile_bindings_with_client_monitors(
            bindings, client_uid="client-A", client_monitor_ids=[0]
        )

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
        assert (b.server_monitor_min_x, b.server_monitor_min_y) == (0, 0)
        assert (b.server_monitor_max_x, b.server_monitor_max_y) == (1920, 1080)

    def test_partial_right_abutment_produces_partial_segment(self):
        # Client smaller than server, vertically centered: overlap is
        # [200, 920) on server's 1080, the full 720 on the client side.
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
        server = _mon(0, 0, 0, 1920, 1080)
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
        server = _mon(0, 0, 0, 1920, 1080)
        placement = self._placement(0, 2020, 0, 1280, 1080)
        assert compute_edge_bindings(placement, [server]) == []

    def test_pixel_tolerance_allows_one_pixel_gap(self):
        # 1-pixel gap still counts so GUI rounding doesn't silently
        # disconnect adjacent boxes.
        server = _mon(0, 0, 0, 1920, 1080)
        placement = self._placement(0, 1921, 0, 1280, 1080)
        out = compute_edge_bindings(placement, [server])
        assert len(out) == 1
        assert out[0].server_edge == Edge.RIGHT

    def test_corner_straddle_produces_two_bindings(self):
        # Client wraps the right+bottom corner of `upper` and abuts the
        # top of `lower_right` simultaneously.
        upper = _mon(0, 0, 0, 1920, 1080)
        lower_right = _mon(1, 1920, 1080, 1920, 1080)
        placement = self._placement(0, 1920, 540, 1280, 540)
        out = compute_edge_bindings(placement, [upper, lower_right])
        edges = sorted((b.server_monitor_id, b.server_edge.value) for b in out)
        assert (0, "right") in edges
        assert (1, "top") in edges

    def test_invalid_zero_size_placement_returns_empty(self):
        server = _mon(0, 0, 0, 1920, 1080)
        placement = self._placement(0, 1920, 0, 0, 0)
        assert compute_edge_bindings(placement, [server]) == []

    def test_edge_binding_contains_server_axis_half_open(self):
        b = compute_edge_bindings(
            self._placement(0, 1920, 0, 1280, 540),
            [_mon(0, 0, 0, 1920, 1080)],
        )[0]
        assert b.contains_server_axis(b.server_axis_start) is True
        assert b.contains_server_axis(b.server_axis_end) is False

    def test_edge_binding_axis_mapping_inverts(self):
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
        for k in (
            "server_monitor_id",
            "server_edge",
            "server_axis_start",
            "server_axis_end",
            "server_monitor_min_x",
            "server_monitor_min_y",
            "server_monitor_max_x",
            "server_monitor_max_y",
            "client_monitor_id",
            "client_edge",
            "client_axis_start",
            "client_axis_end",
        ):
            assert k in d
        assert d["server_edge"] == "right"
        assert d["client_edge"] == "left"


class TestClientObjPlacements:
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
        # Default screen_position is CENTER: no synthetic placement, no
        # bindings. Legacy directional clients get one via the tests below.
        c = self._make_client([])
        server_monitors = [_mon(0, 0, 0, 1920, 1080)]
        assert c.get_edge_bindings(server_monitors) == []


class TestEffectivePlacementSynthesis:
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
        assert ps[0]["workspace_x"] == 1920
        assert ps[0]["workspace_y"] == 0
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
        # Mirror the client's primary so denormalisation stays accurate.
        server = _mon(0, 0, 0, 1920, 1080, primary=True)
        client_mon = _mon(7, 0, 0, 1280, 720, primary=True)
        c = self._make_client("right", monitors=[client_mon])
        ps = c.get_effective_placements([server])
        assert ps[0]["client_monitor_id"] == 7
        assert ps[0]["width"] == 1280
        assert ps[0]["height"] == 720

    def test_real_placements_take_precedence_over_screen_position(self):
        # Explicit placements win over the legacy screen_position.
        server = _mon(0, 0, 0, 1920, 1080, primary=True)
        c = self._make_client("right")
        c.placements = [
            {
                "client_monitor_id": 0,
                "workspace_x": 0,
                "workspace_y": -720,
                "width": 1280,
                "height": 720,
            }
        ]
        ps = c.get_effective_placements([server])
        assert ps == c.placements


class TestEffectivePlacementsExplicitOnly:
    """Only explicit placements drive routing. Unplaced monitors stay
    off the workspace so their OS-level adjacency can't smuggle the
    cursor into an unrouted region that has no return path to the server.
    """

    def _make_client(self, placements, monitors, screen_position="center"):
        from model.client import ClientObj

        return ClientObj(
            uid="client-multi",
            ip_addresses=["10.0.0.3"],
            hostname="client-multi.local",
            screen_position=screen_position,
            monitors=monitors,
            placements=placements,
        )

    def test_unplaced_monitor_stays_off_workspace(self):
        # Admin placed only the primary. The secondary's OS offset must
        # NOT smuggle in a derived placement - that would create a side
        # path the cursor could drift into with no return-to-server.
        primary = MonitorInfo(
            monitor_id=0, min_x=0, min_y=0, max_x=1920, max_y=1080, is_primary=True
        )
        secondary = MonitorInfo(
            monitor_id=1, min_x=0, min_y=1080, max_x=1920, max_y=2160
        )
        explicit = [
            {
                "client_monitor_id": 0,
                "workspace_x": 1920,
                "workspace_y": 0,
                "width": 1920,
                "height": 1080,
            }
        ]
        c = self._make_client(explicit, [primary, secondary])
        ps = c.get_effective_placements([_mon(0, 0, 0, 1920, 1080, primary=True)])
        assert ps == explicit

    def test_all_monitors_placed_explicitly_returned_verbatim(self):
        primary = MonitorInfo(
            monitor_id=0, min_x=0, min_y=0, max_x=1920, max_y=1080, is_primary=True
        )
        secondary = MonitorInfo(
            monitor_id=1, min_x=0, min_y=1080, max_x=1920, max_y=2160
        )
        explicit = [
            {
                "client_monitor_id": 0,
                "workspace_x": 1920,
                "workspace_y": 0,
                "width": 1920,
                "height": 1080,
            },
            {
                "client_monitor_id": 1,
                "workspace_x": -1920,
                "workspace_y": 0,
                "width": 1920,
                "height": 1080,
            },
        ]
        c = self._make_client(explicit, [primary, secondary])
        ps = c.get_effective_placements([_mon(0, 0, 0, 1920, 1080, primary=True)])
        assert ps == explicit

    def test_legacy_screen_position_synthesises_primary_only(self):
        # Pre-layout client (no explicit placements). Legacy synthesis
        # covers the primary; the secondary is intentionally left out.
        primary = MonitorInfo(
            monitor_id=0, min_x=0, min_y=0, max_x=1920, max_y=1080, is_primary=True
        )
        secondary = MonitorInfo(
            monitor_id=1, min_x=0, min_y=1080, max_x=1920, max_y=2160
        )
        c = self._make_client([], [primary, secondary], screen_position="right")
        ps = c.get_effective_placements([_mon(0, 0, 0, 1920, 1080, primary=True)])
        assert len(ps) == 1
        assert ps[0]["client_monitor_id"] == 0


class TestComputeIntraClientBindings:
    @staticmethod
    def _placement(client_monitor_id: int, x: int, y: int, w: int, h: int) -> dict:
        return {
            "client_monitor_id": client_monitor_id,
            "workspace_x": x,
            "workspace_y": y,
            "width": w,
            "height": h,
        }

    def test_single_placement_yields_no_bindings(self):
        assert (
            compute_intra_client_bindings(
                [
                    self._placement(0, 0, 0, 1920, 1080),
                ]
            )
            == []
        )

    def test_vertical_stack_produces_two_bindings(self):
        # primary.BOTTOM <-> secondary.TOP, both directions.
        bindings = compute_intra_client_bindings(
            [
                self._placement(0, 1920, 0, 1920, 1080),
                self._placement(1, 1920, 1080, 1920, 1080),
            ]
        )
        pairs = {(b["src_edge"], b["dst_edge"]) for b in bindings}
        assert ("bottom", "top") in pairs
        assert ("top", "bottom") in pairs
        for b in bindings:
            assert b["src_axis_start"] == 0.0
            assert b["src_axis_end"] == 1.0
            assert b["dst_axis_start"] == 0.0
            assert b["dst_axis_end"] == 1.0

    def test_separated_placements_produce_no_bindings(self):
        # Non-adjacent placements -> void edges, client clamps.
        assert (
            compute_intra_client_bindings(
                [
                    self._placement(0, 1920, 0, 1920, 1080),
                    self._placement(1, -3000, 0, 1920, 1080),
                ]
            )
            == []
        )

    def test_partial_horizontal_overlap_clips_axis(self):
        # Secondary offset right by 200: overlap on x in [200, 1920].
        bindings = compute_intra_client_bindings(
            [
                self._placement(0, 0, 0, 1920, 1080),
                self._placement(1, 200, 1080, 1920, 720),
            ]
        )
        b = next(
            b
            for b in bindings
            if b["src_monitor_id"] == 0 and b["src_edge"] == "bottom"
        )
        assert b["src_axis_start"] == pytest.approx(200 / 1920)
        assert b["src_axis_end"] == pytest.approx(1.0)
        assert b["dst_axis_start"] == pytest.approx(0.0)
        assert b["dst_axis_end"] == pytest.approx((1920 - 200) / 1920)

    def test_pixel_tolerance_allows_small_gap(self):
        # 1 px gap still counts so GUI rounding doesn't sever the warp.
        bindings = compute_intra_client_bindings(
            [
                self._placement(0, 0, 0, 1920, 1080),
                self._placement(1, 0, 1081, 1920, 1080),
            ]
        )
        assert any(
            b["src_monitor_id"] == 0 and b["src_edge"] == "bottom" for b in bindings
        )
