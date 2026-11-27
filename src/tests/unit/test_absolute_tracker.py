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
"""The server-side cursor of a client that cannot report its own.

Bindings come from the real ``compute_edge_bindings`` /
``compute_intra_client_bindings``, so a change to the layout math shows here.
"""

import pytest

from input.mouse._routing import AbsolutePointerTracker, Position, Return
from model.monitor import MonitorInfo
from utils.screen._monitor import compute_edge_bindings, compute_intra_client_bindings

SERVER = [MonitorInfo(0, 0, 0, 1920, 1080, True)]
SERVER_BBOX = (0, 0, 1920, 1080)


def _tracker(client_monitors, placements):
    bindings = [
        b.to_dict() for p in placements for b in compute_edge_bindings(p, SERVER)
    ]
    intra = compute_intra_client_bindings(placements, client_monitors)
    t = AbsolutePointerTracker()
    t.set_topology(client_monitors, bindings, intra, SERVER_BBOX)
    return t


@pytest.fixture
def right_of_server():
    """One 1280x800 client monitor placed right of the server."""
    mon = MonitorInfo(0, 0, 0, 1280, 800, True)
    placement = {
        "client_monitor_id": 0,
        "workspace_x": 1920,
        "workspace_y": 0,
        "width": 1280,
        "height": 800,
    }
    return _tracker([mon], [placement])


class TestActivation:
    def test_landing_is_denormalised_over_the_monitor(self, right_of_server):
        assert right_of_server.activate(0, 0.0, 0.5, "left") == Position(0, 400)

    def test_a_hotkey_switch_lands_at_the_centre(self, right_of_server):
        assert right_of_server.activate(0, -1, -1, None) == Position(640, 400)

    def test_inactive_tracker_ignores_motion(self, right_of_server):
        assert right_of_server.move(10, 0) is None


class TestMotion:
    def test_moves_are_summed(self, right_of_server):
        right_of_server.activate(0, -1, -1, None)
        right_of_server.move(10, 5)
        assert right_of_server.move(-3, 2) == Position(647, 407)

    def test_an_unbound_edge_clamps(self, right_of_server):
        right_of_server.activate(0, -1, -1, None)
        assert right_of_server.move(5000, 0) == Position(1279, 400)
        assert right_of_server.move(0, 5000) == Position(1279, 799)

    def test_subpixel_deltas_are_not_lost(self, right_of_server):
        right_of_server.activate(0, -1, -1, None)
        for _ in range(4):
            right_of_server.move(0.5, 0)
        assert right_of_server.position == Position(642, 400)


class TestReturn:
    def test_the_entry_edge_needs_a_deliberate_push(self, right_of_server):
        right_of_server.activate(0, 0.0, 0.5, "left")
        # A first nudge past the entry edge is absorbed, not a return.
        assert right_of_server.move(-5, 0) == Position(0, 400)
        assert right_of_server.move(-5, 0) == Position(0, 400)
        result = right_of_server.move(-5, 0)

        assert isinstance(result, Return)
        assert not right_of_server.active
        # 6 px inside the server's right edge, normalised over its desktop.
        assert result.x == pytest.approx((1920 - 6) / 1920)
        assert result.y == pytest.approx(400 / 800 * (800 / 1080))

    def test_pushing_back_in_starts_the_gesture_over(self, right_of_server):
        right_of_server.activate(0, 0.0, 0.5, "left")
        right_of_server.move(-10, 0)
        right_of_server.move(1, 0)
        right_of_server.move(-1, 0)  # back on the edge, push restarted
        assert isinstance(right_of_server.move(-5, 0), Position)

    def test_sliding_along_the_edge_keeps_the_push(self, right_of_server):
        right_of_server.activate(0, 0.0, 0.5, "left")
        right_of_server.move(-10, 0)
        right_of_server.move(0, 30)
        assert isinstance(right_of_server.move(-5, 0), Return)

    def test_a_bound_edge_other_than_the_entry_returns_at_once(self):
        # Entered from the top (hotkey-style lock on another edge): the left
        # edge is bound and not locked, so one move through it returns.
        mon = MonitorInfo(0, 0, 0, 1280, 800, True)
        placement = {
            "client_monitor_id": 0,
            "workspace_x": 1920,
            "workspace_y": 0,
            "width": 1280,
            "height": 800,
        }
        t = _tracker([mon], [placement])
        t.activate(0, 0.002, 0.5, "top")
        assert isinstance(t.move(-10, 0), Return)


class TestIntraClient:
    def test_crossing_into_an_adjacent_monitor_follows_the_binding(self):
        left = MonitorInfo(0, 0, 0, 1280, 800, True)
        right = MonitorInfo(1, 1280, 0, 2560, 800)
        placements = [
            {
                "client_monitor_id": 0,
                "workspace_x": 1920,
                "workspace_y": 0,
                "width": 1280,
                "height": 800,
            },
            {
                "client_monitor_id": 1,
                "workspace_x": 3200,
                "workspace_y": 0,
                "width": 1280,
                "height": 800,
            },
        ]
        t = _tracker([left, right], placements)
        t.activate(0, -1, -1, None)

        pos = t.move(700, 0)  # 640 + 700 leaves monitor 0 through its right edge

        assert isinstance(pos, Position)
        assert pos.x >= 1280, "landed on the second monitor"
        assert t.move(10, 0).x == pos.x + 10

    def test_a_smaller_neighbour_clamps_to_its_own_rect(self):
        # Uneven monitors: clamping to the bounding box would leave the model
        # below a monitor's bottom where the compositor cannot go.
        big = MonitorInfo(0, 0, 0, 1280, 1000, True)
        small = MonitorInfo(1, 1280, 0, 2280, 600)
        placements = [
            {
                "client_monitor_id": 0,
                "workspace_x": 1920,
                "workspace_y": 0,
                "width": 1280,
                "height": 1000,
            },
            {
                "client_monitor_id": 1,
                "workspace_x": 3200,
                "workspace_y": 0,
                "width": 1000,
                "height": 600,
            },
        ]
        t = _tracker([big, small], placements)
        t.activate(0, -1, -1, None)
        t.move(0, -400)  # y = 100, inside the band shared with the small one
        t.move(700, 0)  # onto the small monitor

        assert t.move(0, 5000).y == 599


class TestTopologyChange:
    def test_losing_the_current_monitor_deactivates(self, right_of_server):
        right_of_server.activate(0, -1, -1, None)
        right_of_server.set_topology([], [], [], SERVER_BBOX)
        assert not right_of_server.active

    def test_a_resized_monitor_clamps_the_position(self, right_of_server):
        right_of_server.activate(0, -1, -1, None)
        right_of_server.move(600, 0)  # x = 1240
        right_of_server.set_topology(
            [MonitorInfo(0, 0, 0, 1024, 768, True)], [], [], SERVER_BBOX
        )
        assert right_of_server.position == Position(1023, 400)
