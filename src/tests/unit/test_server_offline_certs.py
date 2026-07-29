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

"""Server behaviour when no local IP address can be determined.

Constructing a ``Server`` is what the daemon does to answer SERVICE_CHOICE, long
before anything starts. It must therefore never fail because the machine has no
usable address: TLS certificate setup is deferred, and only an explicit start
reports the problem. An already-provisioned server must also keep its existing
certificates rather than lose them over a failed IP probe.
"""

import pytest

from service import server as server_module
from service.server import Server, ServerStartError
from utils.net import MissingIpError


@pytest.fixture
def offline(monkeypatch):
    """Make every local-IP lookup inside service.server fail."""

    def _no_ip(*_args, **_kwargs):
        raise MissingIpError(
            "Could not determine local IP address ([Errno 51] Network is unreachable)"
        )

    monkeypatch.setattr(server_module, "get_local_ip", _no_ip)
    # Pin the hostname too: certificate generation feeds it into the cert, and
    # letting the real machine name through makes these tests depend on
    # whatever the CI runner happens to be called.
    monkeypatch.setattr(server_module.socket, "gethostname", lambda: "test-host.local")


def _make_server(app_config, server_config) -> Server:
    server_config.enable_ssl()
    return Server(
        app_config=app_config,
        server_config=server_config,
        auto_load_config=False,
    )


def test_server_constructs_without_local_ip(offline, app_config, server_config):
    """No address available: construction succeeds with the setup deferred."""
    server = _make_server(app_config, server_config)

    assert server.certfile is None
    assert server.keyfile is None
    # SSL stays requested: silently downgrading TLS would be worse than a late,
    # explicit failure at start time.
    assert server_config.ssl_enabled is True


@pytest.mark.anyio
async def test_start_reports_missing_ip(
    offline, app_config, server_config, monkeypatch
):
    """The failure surfaces where the user can act on it: starting the server."""
    server = _make_server(app_config, server_config)
    monkeypatch.setattr(Server, "_is_port_available", staticmethod(lambda *_: True))

    with pytest.raises(ServerStartError) as excinfo:
        await server.start()

    assert excinfo.value.reason == "no_local_ip"
    assert "no local network address available" in str(excinfo.value)


def test_reissue_keeps_existing_certificates_when_offline(
    offline, app_config, server_config
):
    """A failed probe must not discard working certificates."""
    server = _make_server(app_config, server_config)

    assert server._cert_manager.generate_ca(force=True)
    assert server._cert_manager.generate_server_certificate(
        hostname="host.local", ip_addresses=["192.168.1.10", "localhost"], force=True
    )
    san_before = server._cert_manager.get_server_cert_san()

    # No exception, and no re-issue attempt: the SAN is left exactly as it was.
    server._reissue_server_cert_if_ip_changed()

    assert server._cert_manager.get_server_cert_san() == san_before
    certfile, keyfile = server._cert_manager.get_server_credentials()
    assert certfile and keyfile


def test_setup_certificates_recovers_when_network_returns(
    offline, app_config, server_config, monkeypatch
):
    """Once an address is available again, the deferred setup succeeds."""
    server = _make_server(app_config, server_config)
    assert server.certfile is None

    monkeypatch.setattr(server_module, "get_local_ip", lambda *_a, **_k: "192.168.1.10")
    certfile, keyfile = server._setup_certificates()

    assert certfile and keyfile
    assert "192.168.1.10" in server._cert_manager.get_server_cert_san()[0]


def test_setup_certificates_recovers_with_unusable_os_hostname(
    offline, app_config, server_config, monkeypatch
):
    """Runner hostnames must not prevent deferred certificate setup."""
    server = _make_server(app_config, server_config)
    assert server.certfile is None

    monkeypatch.setattr(server_module, "get_local_ip", lambda *_a, **_k: "192.168.1.10")
    monkeypatch.setattr(server_module.socket, "gethostname", lambda: "bad host_name")

    certfile, keyfile = server._setup_certificates()

    assert certfile and keyfile
    san_ips, san_dns = server._cert_manager.get_server_cert_san()
    assert "192.168.1.10" in san_ips
    assert "perpetua.local" in san_dns
