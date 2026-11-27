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

"""A placement follows its client monitor's size; the layout editor draws it."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from event import ClientMonitorsUpdatedEvent
from model.client import ClientObj
from model.monitor import MonitorInfo
from service.server import Server
from utils.screen import Screen, compute_edge_bindings, resize_placement

SERVER = [MonitorInfo(0, 0, 0, 1920, 1080, is_primary=True)]


def _placement(x, y, w, h, mid=0):
    return {
        "client_monitor_id": mid,
        "workspace_x": x,
        "workspace_y": y,
        "width": w,
        "height": h,
    }


class TestResizePlacement:
    def test_left_of_the_server_keeps_its_right_edge(self):
        p = resize_placement(_placement(-1280, 0, 1280, 800), 1440, 900, SERVER)
        assert (p["workspace_x"], p["workspace_y"]) == (-1440, 0)
        assert (p["width"], p["height"]) == (1440, 900)

    def test_right_of_the_server_keeps_its_left_edge(self):
        p = resize_placement(_placement(1920, 100, 1280, 800), 960, 600, SERVER)
        assert (p["workspace_x"], p["workspace_y"]) == (1920, 100)

    def test_above_the_server_keeps_its_bottom_edge(self):
        p = resize_placement(_placement(0, -800, 1280, 800), 1440, 900, SERVER)
        assert (p["workspace_x"], p["workspace_y"]) == (0, -900)

    def test_below_the_server_keeps_its_top_edge(self):
        p = resize_placement(_placement(0, 1080, 1280, 800), 1440, 900, SERVER)
        assert (p["workspace_x"], p["workspace_y"]) == (0, 1080)

    @pytest.mark.parametrize(
        "start",
        [(-1280, 0), (1920, 0), (0, -800), (0, 1080)],
        ids=["left", "right", "above", "below"],
    )
    @pytest.mark.parametrize("size", [(1440, 900), (960, 600)], ids=["grow", "shrink"])
    def test_it_stays_bound_to_the_server(self, start, size):
        p = resize_placement(_placement(*start, 1280, 800), *size, SERVER)
        assert compute_edge_bindings(p, SERVER)

    def test_other_keys_survive(self):
        p = resize_placement(dict(_placement(1920, 0, 10, 10), extra=1), 20, 20, SERVER)
        assert p["extra"] == 1


def _client(monitors, placements):
    return ClientObj(
        uid="c1",
        ip_addresses="10.0.0.2",
        monitors=monitors,
        placements=placements,
    )


def _server(monkeypatch):
    server = object.__new__(Server)
    server._logger = MagicMock()
    server.config = MagicMock()
    server.save_config = AsyncMock()
    server.event_bus = MagicMock(dispatch=AsyncMock())
    server._send_notification = AsyncMock()
    monkeypatch.setattr(Screen, "get_monitors_cached", classmethod(lambda cls: SERVER))
    return server


class TestServerFitsPlacements:
    def test_a_monitor_update_resizes_its_placement(self, monkeypatch):
        server = _server(monkeypatch)
        client = _client(
            [MonitorInfo(0, 0, 0, 1280, 800)], [_placement(1920, 0, 1280, 800)]
        )
        update = ClientMonitorsUpdatedEvent(
            client_uid="c1",
            monitors=[MonitorInfo(0, 0, 0, 960, 600).to_dict()],
        )

        asyncio.run(server._apply_client_monitors_update(client, update))

        assert client.placements == [_placement(1920, 0, 960, 600)]
        server.config.clients_manager.update_client.assert_called_with(client)
        server.save_config.assert_awaited()

    def test_a_connect_resizes_a_placement_left_stale_while_away(self, monkeypatch):
        # The handshake replaces the monitor list directly, so the update
        # path never sees the change.
        server = _server(monkeypatch)
        monkeypatch.setattr(
            "utils.screen._base.invalidate_monitors_cache", lambda: None
        )
        client = _client(
            [MonitorInfo(0, 0, 0, 1440, 900)], [_placement(-1280, 0, 1280, 800)]
        )

        asyncio.run(server._on_client_connected(client, []))

        assert client.placements == [_placement(-1440, 0, 1440, 900)]
        event = server.event_bus.dispatch.await_args.kwargs["data"]
        assert event.edge_bindings, "bindings are computed on the fitted size"

    def test_matching_sizes_and_unknown_monitors_are_left_alone(self, monkeypatch):
        server = _server(monkeypatch)
        same = _placement(1920, 0, 1280, 800, mid=0)
        unknown = _placement(0, 1080, 640, 480, mid=7)
        client = _client([MonitorInfo(0, 0, 0, 1280, 800)], [same, unknown])

        assert server._fit_placements_to_monitors(client, SERVER) is False
        assert client.placements == [same, unknown]
