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

"""Tests for re-issuing the server leaf certificate after an IP change.

The server leaf bakes the machine's IP into its SAN. When the IP changes (DHCP
rebind, network switch) the old SAN no longer matches and clients fail TLS
verification with "IP address mismatch". The fix re-issues ONLY the leaf,
signed by the same CA, so already-paired clients keep trusting the server. These
tests pin down the SAN reader and the CA-preserving re-issue behaviour.
"""

from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization

from utils.crypto import CertificateManager


def _load(path) -> x509.Certificate:
    with open(path, "rb") as f:
        return x509.load_pem_x509_certificate(f.read(), default_backend())


def _san_ips(cm: CertificateManager) -> list[str]:
    return cm.get_server_cert_san()[0]


def test_get_server_cert_san_returns_empty_when_missing(tmp_path):
    cm = CertificateManager(tmp_path / "server")
    assert cm.get_server_cert_san() == ([], [])


def test_get_server_cert_san_splits_ips_and_dns(tmp_path):
    cm = CertificateManager(tmp_path / "server")
    assert cm.generate_ca(force=True)
    assert cm.generate_server_certificate(
        hostname="host.local", ip_addresses=["192.168.1.10", "localhost"], force=True
    )
    ips, dns = cm.get_server_cert_san()
    assert "192.168.1.10" in ips
    assert "host.local" in dns
    # "localhost" is not a valid IP literal, so it lands among the DNS names.
    assert "localhost" in dns


def test_reissue_preserves_ca_and_updates_san(tmp_path):
    cm = CertificateManager(tmp_path / "server")
    assert cm.generate_ca(force=True)
    assert cm.generate_server_certificate(
        hostname="host.local", ip_addresses=["192.168.1.10", "localhost"], force=True
    )

    ca_before = _load(cm.ca_cert_path)
    assert "192.168.1.10" in _san_ips(cm)
    assert "10.0.0.5" not in _san_ips(cm)

    # Re-issue the leaf with the new IP unioned onto the old ones (the exact
    # union Server._reissue_server_cert_if_ip_changed builds).
    assert cm.generate_server_certificate(
        hostname="host.local",
        ip_addresses=["192.168.1.10", "10.0.0.5", "localhost"],
        force=True,
    )

    ca_after = _load(cm.ca_cert_path)
    leaf_after = _load(cm.server_cert_path)

    # The CA is untouched: same serial and identical bytes, so already-paired
    # clients still trust the chain — no re-pairing required.
    pem = serialization.Encoding.PEM
    assert ca_after.serial_number == ca_before.serial_number
    assert ca_after.public_bytes(pem) == ca_before.public_bytes(pem)

    # The new leaf is still issued by that CA and now covers BOTH IPs.
    assert leaf_after.issuer == ca_after.subject
    san_ips = _san_ips(cm)
    assert "192.168.1.10" in san_ips
    assert "10.0.0.5" in san_ips
