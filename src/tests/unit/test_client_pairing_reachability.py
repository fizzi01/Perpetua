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

"""Pairing must not be attempted against an unreachable server.

``_handle_server_availability`` lets an unreachable-but-configured server
through so the reconnect loop can pick it up later. That is right for a paired
client, but an unpaired one used to walk straight into an OTP prompt that could
never be redeemed - the pairing exchange needs the server live, and the
ConnectionHandler cannot even be built without a CA. ``start()`` now fails with
the reason instead.
"""

import asyncio
import itertools
from unittest.mock import AsyncMock, MagicMock

import pytest

from event.notification import NotificationEventType
from daemon import Daemon
from service.client import Client
from utils.crypto.sharing import CertificateReceiveError


@pytest.fixture
def make_client(tmp_path, monkeypatch):
    """Client with an isolated on-disk config, built inside the running loop."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / ".state"))

    def _make():
        return Client(auto_load_config=False)

    return _make


def _arm(client, reachable):
    """Aim an unpaired TLS client at a server that is up or down."""
    client.config.set_server_connection(
        host="192.0.2.1", port=55655, auto_reconnect=True
    )
    client.config.ssl_enabled = True
    events = []
    client._notification_callback = AsyncMock(side_effect=lambda e: events.append(e))
    client._probe_tcp = AsyncMock(return_value=reachable)
    client.discover_servers = AsyncMock(return_value=None)
    client._found_services = []
    client.request_pairing = AsyncMock(return_value=(False, 0, "ERROR"))
    return events


@pytest.mark.anyio
async def test_start_fails_instead_of_prompting_otp(make_client):
    """Unpaired + unreachable: no OTP prompt, and the error says why."""
    client = make_client()
    events = _arm(client, reachable=False)
    assert client._needs_pairing() is True

    with pytest.raises(CertificateReceiveError) as excinfo:
        await asyncio.wait_for(client.start(), timeout=10)

    assert "not reachable" in str(excinfo.value)
    assert NotificationEventType.OTP_NEEDED not in [e.event_type for e in events]


@pytest.mark.anyio
async def test_start_still_prompts_otp_when_server_answers(make_client):
    """Unpaired + reachable: the pairing prompt must be unaffected."""
    client = make_client()
    events = _arm(client, reachable=True)

    task = asyncio.ensure_future(client.start())
    try:
        for _ in range(60):
            await asyncio.sleep(0.05)
            if any(e.event_type == NotificationEventType.OTP_NEEDED for e in events):
                break
        assert any(e.event_type == NotificationEventType.OTP_NEEDED for e in events)
    finally:
        task.cancel()
        try:
            await task
        except BaseException:  # noqa: BLE001 - teardown only
            pass


@pytest.mark.anyio
async def test_paired_client_still_proceeds_to_retry_loop(make_client, monkeypatch):
    """Paired + unreachable: the initial-connect retry behaviour is preserved."""
    client = make_client()
    _arm(client, reachable=False)
    monkeypatch.setattr(client, "_needs_pairing", lambda: False)
    monkeypatch.setattr(
        client._cert_manager, "get_ca_cert_path", lambda **_k: "/ca.pem"
    )
    monkeypatch.setattr(
        client._cert_manager, "get_client_credentials", lambda: ("/c.pem", "/c.key")
    )
    monkeypatch.setattr(client, "_initialize_streams", AsyncMock(return_value=None))
    monkeypatch.setattr(client, "_get_enabled_stream_types", lambda: [])

    class _Handler:
        def __init__(self, **_kw):
            pass

        async def start(self):
            return True

    monkeypatch.setattr("service.client.ConnectionHandler", _Handler)

    assert await asyncio.wait_for(client.start(), timeout=10) is True


@pytest.mark.anyio
async def test_needs_pairing_matches_replaced_condition(make_client, monkeypatch):
    """``_needs_pairing`` folds the old nested check without changing it."""

    def original(ssl_enabled, has_cert, creds, ca_path):
        needs_client_identity = not creds
        if ssl_enabled and (not has_cert or needs_client_identity):
            if not ca_path or needs_client_identity:
                return True
        return False

    client = make_client()
    for ssl_enabled, has_cert, creds, ca_path in itertools.product(
        [True, False], [True, False], [True, False], ["/ca.pem", None]
    ):
        client.config.ssl_enabled = ssl_enabled
        monkeypatch.setattr(client, "has_certificate", lambda hc=has_cert: hc)
        monkeypatch.setattr(
            client._cert_manager, "client_credentials_exist", lambda cr=creds: cr
        )
        monkeypatch.setattr(
            client._cert_manager, "get_ca_cert_path", lambda cp=ca_path, **_k: cp
        )
        assert client._needs_pairing() == original(
            ssl_enabled, has_cert, creds, ca_path
        ), (ssl_enabled, has_cert, creds, ca_path)


@pytest.mark.anyio
async def test_daemon_reports_reason_not_unhandled_error(monkeypatch, tmp_path):
    """The daemon forwards the pairing reason instead of a crash-looking log."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    daemon = Daemon()
    reason = "Cannot pair with the server: it is not reachable."
    daemon._client = MagicMock()
    daemon._client.is_running.return_value = False
    daemon._client.start = AsyncMock(side_effect=CertificateReceiveError(reason))
    daemon._server = None

    errors = []
    daemon._notification_manager.notify_command_error = AsyncMock(
        side_effect=lambda cmd, msg, **_k: errors.append((cmd, msg))
    )
    unhandled = []
    monkeypatch.setattr(
        daemon._logger,
        "error",
        lambda m, *_a, **_k: unhandled.append(m) if m == "Unhandled error" else None,
    )

    await daemon._handle_start_client({})

    assert errors == [("start_client", reason)]
    assert not unhandled
