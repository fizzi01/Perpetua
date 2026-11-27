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

"""A client's RemoteDesktop consent reaches the server's GUI."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from command import CommandHandler
from event import (
    BusEventType,
    ClientInputConsentCommandEvent,
    ClientInputConsentEvent,
)
from event.notification import NotificationEventType
from model.client import ClientObj
from network.protocol.message import MessageType
from network.stream.handler import StreamHandler
from service.server import Server


def _handler():
    bus = MagicMock(dispatch=AsyncMock())
    return CommandHandler(event_bus=bus, stream=MagicMock()), bus


class TestCommandHandler:
    def test_a_consent_command_becomes_a_bus_event(self, monkeypatch):
        handler, bus = _handler()
        cmd = ClientInputConsentCommandEvent(
            client_uid="c1", state="denied", reason="not answered"
        )
        asyncio.run(handler.handle_client_input_consent(cmd))

        kwargs = bus.dispatch.await_args.kwargs
        assert kwargs["event_type"] == BusEventType.CLIENT_INPUT_CONSENT_CHANGED
        assert kwargs["data"].to_dict() == {
            "client_uid": "c1",
            "state": "denied",
            "reason": "not answered",
        }

    @pytest.mark.parametrize("uid, state", [("", "pending"), ("c1", "bogus")])
    def test_a_malformed_command_is_dropped(self, uid, state):
        handler, bus = _handler()
        cmd = ClientInputConsentCommandEvent(client_uid=uid, state=state)
        asyncio.run(handler.handle_client_input_consent(cmd))
        bus.dispatch.assert_not_awaited()

    def test_a_wire_message_round_trips(self):
        handler, bus = _handler()
        cmd = ClientInputConsentCommandEvent(client_uid="c1", state="pending")
        message = MagicMock(
            message_type=MessageType.COMMAND,
            payload=cmd.to_dict(),
            source="right",
            target="server",
        )
        asyncio.run(handler.handle_command(message))

        data = bus.dispatch.await_args.kwargs["data"]
        assert (data.client_uid, data.state, data.reason) == ("c1", "pending", None)


class TestServerNotifiesTheGui:
    def _server(self, client):
        server = object.__new__(Server)
        server._logger = MagicMock()
        server.config = MagicMock()
        server.config.get_client.side_effect = lambda uid: (
            client if client and uid == client.uid else None
        )
        server._send_notification = AsyncMock()
        return server

    def test_the_gui_hears_which_client_and_why(self):
        client = ClientObj(uid="c1", ip_addresses="10.0.0.2", hostname="vm")
        server = self._server(client)
        asyncio.run(
            server._on_client_input_consent(
                ClientInputConsentEvent("c1", "denied", "not answered")
            )
        )
        event = server._send_notification.await_args.args[0]
        assert event.event_type == NotificationEventType.INPUT_CONSENT
        assert event.data["state"] == "denied"
        assert event.data["reason"] == "not answered"
        assert event.data["client_name"] == "vm"
        assert event.data["client_uid"] == "c1"

    def test_an_unknown_client_is_ignored(self):
        server = self._server(None)
        asyncio.run(
            server._on_client_input_consent(ClientInputConsentEvent("zz", "pending"))
        )
        server._send_notification.assert_not_awaited()


class _Stream(StreamHandler):
    async def _core_sender(self):
        while True:
            await self._send_queue.get()


class TestFlush:
    def _stream(self):
        stream = object.__new__(_Stream)
        stream._send_queue = asyncio.Queue()
        stream._active = True
        return stream

    def test_it_waits_for_the_queue_to_drain(self):
        async def run():
            stream = self._stream()
            await stream.send("last")
            sender = asyncio.ensure_future(stream._core_sender())
            try:
                assert await stream.flush(timeout=1.0) is True
                assert stream._send_queue.empty()
            finally:
                sender.cancel()

        asyncio.run(run())

    def test_it_gives_up_at_the_timeout(self):
        async def run():
            stream = self._stream()
            await stream.send("stuck")
            return await stream.flush(timeout=0.05)

        assert asyncio.run(run()) is False
