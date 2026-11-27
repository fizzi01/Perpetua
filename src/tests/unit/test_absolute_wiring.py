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
"""Plumbing around the server-tracked cursor: handshake capability, the
mouse-stream filter, and the Wayland client geometry from libei regions."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from model.client import ClientObj
from model.monitor import MonitorInfo
from network.protocol.message import MessageBuilder
from network.stream.handler import _ServerStreamHandler
from utils.screen._linux import _merge_regions


class TestHandshakeCapability:
    def test_the_handshake_carries_capabilities(self):
        msg = MessageBuilder().create_handshake_message(
            client_name="c",
            screen_resolution="1x1",
            capabilities={"cursor_feedback": False},
        )
        assert msg.payload["capabilities"] == {"cursor_feedback": False}

    def test_the_capability_is_never_persisted(self):
        # It describes this connection, not the client: a client that moves
        # from Wayland to X11 must not inherit "no feedback" from the config.
        client = ClientObj(uid="c")
        client.cursor_feedback = False
        assert "cursor_feedback" not in client.to_dict()
        assert ClientObj.from_dict(client.to_dict()).cursor_feedback is True


class TestOutboundFilter:
    @staticmethod
    def _handler(outbound_filter):
        handler = object.__new__(_ServerStreamHandler)
        handler._active_client = MagicMock()
        handler._send_queue = asyncio.Queue()
        handler._outbound_filter = outbound_filter
        handler.msg_exchange = MagicMock(send_stream_type_message=AsyncMock())
        handler.stream_type = 1
        handler.source = "server"
        return handler

    @pytest.mark.anyio
    async def test_the_filter_rewrites_what_is_sent(self):
        seen = []

        async def rewrite(data, client):
            seen.append(client)
            return {"rewritten": data["n"] + 1}

        handler = self._handler(rewrite)
        await handler._send_queue.put({"n": 1})
        await handler._send_logic()

        kwargs = handler.msg_exchange.send_stream_type_message.await_args.kwargs
        assert kwargs["rewritten"] == 2
        assert seen == [handler._active_client]

    @pytest.mark.anyio
    async def test_none_drops_the_event(self):
        async def drop(data, client):
            return None

        handler = self._handler(drop)
        await handler._send_queue.put({"n": 1})
        await handler._send_logic()

        handler.msg_exchange.send_stream_type_message.assert_not_awaited()


class TestWaylandGeometryFromRegions:
    def test_regions_replace_the_physical_mode_sizes(self):
        # 200% on a 3840x2160 output: wl_output reports the mode, the region
        # the logical size the compositor (and absolute motion) uses.
        outputs = [MonitorInfo(0, 0, 0, 3840, 2160, True, "DP-1")]
        merged = _merge_regions(outputs, [(0, 0, 1920, 1080, 2.0)])

        assert merged == [MonitorInfo(0, 0, 0, 1920, 1080, True, "DP-1", 2.0)]

    def test_ids_follow_the_output_at_the_same_origin(self):
        outputs = [
            MonitorInfo(0, 0, 0, 2560, 1440, True, "DP-1"),
            MonitorInfo(1, 1280, 0, 3200, 1080, False, "HDMI-1"),
        ]
        merged = _merge_regions(
            outputs, [(1280, 0, 1920, 1080, 1.0), (0, 0, 1280, 720, 2.0)]
        )

        assert {m.name: m.monitor_id for m in merged} == {"DP-1": 0, "HDMI-1": 1}

    def test_an_unknown_region_gets_a_fresh_id_and_a_primary_exists(self):
        merged = _merge_regions([], [(0, 0, 1280, 800, 1.0)])
        assert merged[0].monitor_id == 0 and merged[0].is_primary
