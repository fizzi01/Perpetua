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

"""Tests for the client initial-connect retry + dynamic-IP + connecting event.

Covers:
* ``ConnectingEvent`` serialization (new notification type).
* ``ConnectionHandler.update_target`` retargeting.
* ``Client._has_server_configured`` predicate.
* ``Client._handle_server_availability`` no-services branch: proceed-and-retry
  when a server is configured and auto-reconnect is on; fast-fail otherwise.
"""

from unittest.mock import AsyncMock

import pytest

from event.notification import ConnectingEvent, NotificationEventType
from network.connection.client import ConnectionHandler
from service.client import Client


# ---------------------------------------------------------------------------
# ConnectingEvent
# ---------------------------------------------------------------------------
def test_connecting_event_serialization():
    ev = ConnectingEvent(connection_data={"host": "10.0.0.5", "port": 55655})
    d = ev.to_dict()
    assert d["event_type"] == NotificationEventType.CONNECTING.value == "connecting"
    assert d["data"]["host"] == "10.0.0.5"
    assert d["message"]


# ---------------------------------------------------------------------------
# ConnectionHandler.update_target
# ---------------------------------------------------------------------------
def test_update_target_changes_host_port():
    h = ConnectionHandler(host="1.1.1.1", port=1000, use_ssl=False)
    h.update_target("2.2.2.2", 2000)
    assert h.host == "2.2.2.2"
    assert h.port == 2000


def test_update_target_ignores_empty():
    h = ConnectionHandler(host="1.1.1.1", port=1000, use_ssl=False)
    h.update_target("", 0)
    assert h.host == "1.1.1.1"
    assert h.port == 1000


# ---------------------------------------------------------------------------
# Client predicates / retry gate
# ---------------------------------------------------------------------------
@pytest.fixture
def make_client(tmp_path, monkeypatch):
    """Factory for a Client with an isolated on-disk config (no autoload).

    Returned as a factory (not a prebuilt instance) so async tests construct
    the Client inside the running event loop - the constructor creates
    ``asyncio.Future``/``Lock`` objects that must bind to the active loop.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / ".state"))

    def _make():
        return Client(auto_load_config=False)

    return _make


# These build a Client (whose constructor allocates asyncio.Future/Lock), so
# they run under anyio's loop even though the predicate itself is sync.
@pytest.mark.anyio
async def test_has_server_configured_host_only(make_client):
    client = make_client()
    client.config.set_server_connection(host="10.0.0.9", port=55655)
    assert client._has_server_configured() is True


@pytest.mark.anyio
async def test_has_server_configured_hostname_only(make_client):
    client = make_client()
    client.config.set_server_connection(hostname="srv.local", port=55655)
    assert client._has_server_configured() is True


@pytest.mark.anyio
async def test_has_server_configured_missing_port(make_client):
    client = make_client()
    client.config.set_server_connection(host="10.0.0.9", port=0)
    assert client._has_server_configured() is False


@pytest.mark.anyio
async def test_has_server_configured_nothing(make_client):
    client = make_client()
    assert client._has_server_configured() is False


@pytest.mark.anyio
async def test_availability_proceeds_when_configured_and_autoreconnect(make_client):
    """Configured server + auto-reconnect + nothing discovered -> proceed (retry)."""
    client = make_client()
    client.config.set_server_connection(
        host="10.0.0.9", port=55655, auto_reconnect=True
    )
    client._found_services = []
    client.check_server_availability = AsyncMock(return_value=False)
    # The one-shot probe must NOT be used on the retry path.
    client._is_server_available = AsyncMock(return_value=False)

    result = await client._handle_server_availability()

    assert result is True
    client._is_server_available.assert_not_called()


@pytest.mark.anyio
async def test_availability_fastfails_when_autoreconnect_disabled(make_client):
    """Auto-reconnect off -> keep the one-shot probe / fast-fail."""
    client = make_client()
    client.config.set_server_connection(
        host="10.0.0.9", port=55655, auto_reconnect=False
    )
    client._found_services = []
    client.check_server_availability = AsyncMock(return_value=False)
    client._is_server_available = AsyncMock(return_value=False)

    result = await client._handle_server_availability()

    assert result is False
    client._is_server_available.assert_awaited_once()
