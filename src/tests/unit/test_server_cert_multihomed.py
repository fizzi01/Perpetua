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

"""Certificate SAN on a multi-homed server.

The client passes the dialed IP as ``server_hostname``, so that exact address
must appear in the leaf's SAN or the handshake fails with "IP address
mismatch". The old staleness test was ``current_ip in san_ips`` with
``current_ip`` = the default-route address - which on a multi-homed host is
*already* in the SAN, so the leaf was never re-issued and the address the
admin actually selected was never covered.

Real certificates in tmp_path, no mocks: the house style for crypto.
"""

from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding

from service import server as server_module
from service.server import Server
from utils.net._base import LocalInterface


def _iface(ip, name, default=False):
    return LocalInterface(
        name=name,
        display_name=name,
        ip=ip,
        prefix=24,
        cidr=f"{ip.rsplit('.', 1)[0]}.0/24",
        is_default_route=default,
    )


TWO_LINKS = [
    _iface("192.168.1.20", "en0", default=True),
    _iface("10.0.0.1", "eth1"),
]


def _make_server(app_config, server_config, monkeypatch, interfaces=TWO_LINKS):
    # Pin the hostname: it lands in the cert, and a CI runner's name would
    # make these assertions machine-dependent.
    monkeypatch.setattr(server_module.socket, "gethostname", lambda: "test-host.local")
    # Patch the enumeration itself, not ``_iface_snapshot``: the certificate is
    # generated inside ``Server.__init__``, before any snapshot can be
    # assigned, so the real machine's address would otherwise leak into the SAN
    # and make these assertions depend on the host running them.
    _set_interfaces(monkeypatch, interfaces)
    server_config.enable_ssl()
    return Server(
        app_config=app_config,
        server_config=server_config,
        auto_load_config=False,
    )


def _set_interfaces(monkeypatch, interfaces):
    from utils import net as net_module

    monkeypatch.setattr(
        net_module, "list_local_interfaces", lambda *a, **k: list(interfaces)
    )


def _ca_fingerprint(server) -> bytes:
    with open(server._cert_manager.ca_cert_path, "rb") as f:
        ca = x509.load_pem_x509_certificate(f.read(), default_backend())
    return ca.fingerprint(hashes.SHA256())


def _leaf_bytes(server) -> bytes:
    with open(server._cert_manager.server_cert_path, "rb") as f:
        return f.read()


class TestFirstGeneration:
    def test_san_covers_every_advertised_address(
        self, app_config, server_config, monkeypatch
    ):
        """Not just the default-route one - that was the whole bug."""
        server = _make_server(app_config, server_config, monkeypatch)

        san_ips, _ = server._cert_manager.get_server_cert_san()

        assert "192.168.1.20" in san_ips
        assert "10.0.0.1" in san_ips

    def test_san_includes_loopback(self, app_config, server_config, monkeypatch):
        """Costs nothing and makes same-host smoke testing work."""
        server = _make_server(app_config, server_config, monkeypatch)

        san_ips, san_dns = server._cert_manager.get_server_cert_san()

        assert "127.0.0.1" in san_ips
        assert "localhost" in san_dns

    def test_explicit_choice_still_covers_the_others(
        self, app_config, server_config, monkeypatch
    ):
        """A client mid-reconnect may still dial the address we moved off."""
        server_config.host = "eth1"
        server = _make_server(app_config, server_config, monkeypatch)

        san_ips, _ = server._cert_manager.get_server_cert_san()

        assert "10.0.0.1" in san_ips
        assert "192.168.1.20" in san_ips


class TestReissue:
    def test_no_reissue_when_every_address_is_covered(
        self, app_config, server_config, monkeypatch
    ):
        """Steady state must not churn the key material every 30s."""
        server = _make_server(app_config, server_config, monkeypatch)
        before = _leaf_bytes(server)

        server._reissue_server_cert_if_ip_changed()

        assert _leaf_bytes(server) == before

    def test_reissue_when_one_of_several_is_missing(
        self, app_config, server_config, monkeypatch
    ):
        """The case the old single-value predicate could never detect."""
        server = _make_server(
            app_config, server_config, monkeypatch, interfaces=[TWO_LINKS[0]]
        )
        san_ips, _ = server._cert_manager.get_server_cert_san()
        assert "10.0.0.1" not in san_ips

        # The second link appears (cable plugged in).
        _set_interfaces(monkeypatch, TWO_LINKS)
        server._reissue_server_cert_if_ip_changed()

        san_ips, _ = server._cert_manager.get_server_cert_san()
        assert "10.0.0.1" in san_ips

    def test_reissue_preserves_old_san_entries(
        self, app_config, server_config, monkeypatch
    ):
        """The SAN grows monotonically so no in-flight client is cut off."""
        server = _make_server(
            app_config, server_config, monkeypatch, interfaces=[TWO_LINKS[0]]
        )

        _set_interfaces(monkeypatch, [_iface("172.16.5.5", "eth9")])
        server._reissue_server_cert_if_ip_changed()

        san_ips, san_dns = server._cert_manager.get_server_cert_san()
        assert "172.16.5.5" in san_ips
        assert "192.168.1.20" in san_ips, "old address must remain valid"
        assert "test-host.local" in san_dns

    def test_reissue_keeps_the_ca_so_paired_clients_survive(
        self, app_config, server_config, monkeypatch
    ):
        """The upgrade guarantee: no re-pairing.

        A paired client validates the new leaf against the CA it already
        holds, so the CA must be byte-identical and the chain must still
        verify.
        """
        server = _make_server(
            app_config, server_config, monkeypatch, interfaces=[TWO_LINKS[0]]
        )
        ca_before = _ca_fingerprint(server)
        leaf_before = _leaf_bytes(server)

        _set_interfaces(monkeypatch, TWO_LINKS)
        server._reissue_server_cert_if_ip_changed()

        assert _ca_fingerprint(server) == ca_before
        assert _leaf_bytes(server) != leaf_before

        # The new leaf verifies against the unchanged CA.
        with open(server._cert_manager.ca_cert_path, "rb") as f:
            ca = x509.load_pem_x509_certificate(f.read(), default_backend())
        leaf = x509.load_pem_x509_certificate(_leaf_bytes(server), default_backend())
        assert leaf.issuer == ca.subject
        ca.public_key().verify(
            leaf.signature,
            leaf.tbs_certificate_bytes,
            padding.PKCS1v15(),
            leaf.signature_hash_algorithm,
        )

    def test_no_usable_address_leaves_certificates_alone(
        self, app_config, server_config, monkeypatch
    ):
        """An offline machine must not lose the certs it will need later."""
        from utils.net import MissingIpError

        server = _make_server(app_config, server_config, monkeypatch)
        before = _leaf_bytes(server)

        _set_interfaces(monkeypatch, [])

        def _no_ip(*_a, **_k):
            raise MissingIpError("offline")

        monkeypatch.setattr(server_module, "get_local_ip", _no_ip)

        server._reissue_server_cert_if_ip_changed()

        assert _leaf_bytes(server) == before


class TestHostnameIsNotTheBindAddress:
    def test_cert_hostname_comes_from_the_os_not_config(
        self, app_config, server_config, monkeypatch
    ):
        """``config.host`` is an interface preference and must never leak in.

        It can legitimately hold an adapter *name*, which is not a DNS name
        and has no business in a certificate.
        """
        server_config.host = "eth1"
        server = _make_server(app_config, server_config, monkeypatch)

        _, san_dns = server._cert_manager.get_server_cert_san()

        assert "test-host.local" in san_dns
        assert "eth1" not in san_dns
