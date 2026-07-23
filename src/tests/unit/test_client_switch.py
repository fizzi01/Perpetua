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

"""Tests for the clean-up performed when the client switches server.

When a client picks a *different* server than the one it already trusts,
``Client._forget_previous_server`` must drop the old server's trust material:
its pinned CA (``ca_<id>.crt`` + every mapping alias) and the machine's client
identity (signed by the old CA, worthless against the new one). This forces a
fresh OTP re-pairing with the new server instead of retrying stale credentials.
"""

import pytest

from utils.crypto import CertificateManager
from service.client import Client


@pytest.fixture
async def switch_setup(tmp_path):
    """A client trusting server A, with isolated on-disk cert stores.

    Returns ``(client, server_cm, client_cm)`` where ``server_cm`` has already
    minted a CA + server certificate and ``client_cm`` points at a separate
    temp directory. The client is built without touching disk config.

    Async so ``Client()`` is instantiated inside a running event loop: its
    ``asyncio.Future`` fields (see ``Client.__init__``) require one on 3.12+.
    """
    server_dir = tmp_path / "server"
    client_dir = tmp_path / "client"
    server_dir.mkdir()
    client_dir.mkdir()

    # Server A: mint a CA and issue a client certificate.
    server_cm = CertificateManager(server_dir)
    server_cm.generate_ca()
    server_cm.generate_server_certificate(
        ip_addresses=["127.0.0.1"], hostname="server-a.local"
    )

    # Build a Client without touching disk config, then point its cert
    # manager at an isolated temp directory.
    client = Client(auto_load_config=False)
    client._cert_manager = CertificateManager(client_dir)

    return client, server_cm, client._cert_manager


def _pair_with_server_a(server_cm: CertificateManager, client_cm: CertificateManager):
    """Seed the client with server A's CA and a client identity."""
    csr_pem = client_cm.generate_client_key_and_csr()
    cert_pem = server_cm.sign_client_csr(csr_pem, "client-uid")
    assert client_cm.save_client_certificate(cert_pem)
    with open(server_cm.ca_cert_path, "rb") as f:
        assert client_cm.save_ca_data(f.read(), "A")

@pytest.mark.anyio
async def test_forget_previous_server_wipes_ca_and_client_identity(switch_setup):
    client, server_cm, client_cm = switch_setup
    _pair_with_server_a(server_cm, client_cm)
    # Precondition: both trust artefacts are present.
    assert client_cm.get_ca_cert_path("A") is not None
    assert client_cm.client_credentials_exist()

    client._forget_previous_server("A", "127.0.0.1", "server-a.local")

    # The old CA and the stale client identity are both gone.
    assert client_cm.get_ca_cert_path("A") is None
    assert not client_cm.client_credentials_exist()

@pytest.mark.anyio
async def test_forget_previous_server_is_best_effort_when_nothing_stored(switch_setup):
    client, _server_cm, client_cm = switch_setup
    # No CA and no client identity: cleanup must not raise.
    assert client_cm.get_ca_cert_path("A") is None
    assert not client_cm.client_credentials_exist()

    client._forget_previous_server("A", "127.0.0.1", "server-a.local")

    assert client_cm.get_ca_cert_path("A") is None
    assert not client_cm.client_credentials_exist()
