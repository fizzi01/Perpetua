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

"""One connection attempt uses one endpoint, start to finish.

``update_target`` can land between the command stream and the data streams -
the discovery loop calls it whenever it retargets. Both used to read
``self.host`` fresh, so the streams of a single connection could be split
across two addresses. The server correlates streams by peer IP, so the strays
arrive as orphans and the handshake half-completes: a failure mode that looks
like a flaky network rather than a bug.
"""

import asyncio

import pytest

from network.connection.client import ConnectionHandler


@pytest.fixture
def handler():
    return ConnectionHandler(host="10.0.0.1", port=5555, use_ssl=False)


class TestTargetPinning:
    def test_update_target_does_not_disturb_a_pinned_attempt(self, handler):
        handler._target = ("10.0.0.1", 5555)

        handler.update_target("192.168.1.20", 6000)

        # The next attempt will pick the new values up from host/port; the one
        # already in flight keeps the endpoint it started on.
        assert handler.host == "192.168.1.20"
        assert handler._target == ("10.0.0.1", 5555)

    @pytest.mark.anyio
    async def test_streams_dial_the_pinned_endpoint(self, handler, monkeypatch):
        """The regression: a retarget mid-connection split the streams."""
        handler._target = ("10.0.0.1", 5555)
        handler.update_target("192.168.1.20", 6000)

        dialed = []

        async def _record(host, port, **_kwargs):
            dialed.append((host, port))
            raise ConnectionRefusedError("stop here; the endpoint is what matters")

        monkeypatch.setattr(asyncio, "open_connection", _record)

        await handler._open_additional_streams([1])

        assert dialed == [("10.0.0.1", 5555)]

    @pytest.mark.anyio
    async def test_streams_fall_back_to_live_values_when_unpinned(
        self, handler, monkeypatch
    ):
        """Reconnect paths can open streams without a fresh _connect()."""
        handler._target = None
        dialed = []

        async def _record(host, port, **_kwargs):
            dialed.append((host, port))
            raise ConnectionRefusedError("stop here")

        monkeypatch.setattr(asyncio, "open_connection", _record)

        await handler._open_additional_streams([1])

        assert dialed == [("10.0.0.1", 5555)]

    @pytest.mark.anyio
    async def test_connect_pins_the_endpoint_it_dials(self, handler, monkeypatch):
        dialed = []

        async def _record(host, port, **_kwargs):
            dialed.append((host, port))
            raise ConnectionRefusedError("stop here")

        monkeypatch.setattr(asyncio, "open_connection", _record)

        await handler._connect()

        assert handler._target == ("10.0.0.1", 5555)
        assert dialed == [("10.0.0.1", 5555)]
