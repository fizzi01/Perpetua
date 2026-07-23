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

"""Tests for the clock-skew tolerance applied at certificate issuance.

Peers on a LAN often run without NTP; a verifier whose clock trails the issuer
used to reject a freshly-minted chain with "certificate is not yet valid". The
fix backdates every cert's ``notBefore`` by :data:`CLOCK_SKEW_TOLERANCE` while
keeping the total validity span equal to the requested lifetime. These tests
pin both properties down and prove a trailing-clock verifier accepts the chain.
"""

import datetime

import pytest
from cryptography import x509
from cryptography.hazmat.backends import default_backend

from utils.crypto import CLOCK_SKEW_TOLERANCE, CertificateManager

# The cert time fields are stored with whole-second resolution, and issuance
# takes a non-zero amount of wall-clock time (RSA keygen). Bracket assertions
# allow this much slack around the observed generation window.
_SLACK = datetime.timedelta(seconds=5)

_CA_LIFETIME = datetime.timedelta(days=3650)
_LEAF_LIFETIME = datetime.timedelta(days=365)


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _load(path) -> x509.Certificate:
    with open(path, "rb") as f:
        return x509.load_pem_x509_certificate(f.read(), default_backend())


@pytest.fixture
def cert_managers(tmp_path):
    """A server and client :class:`CertificateManager` on isolated temp dirs."""
    server_cm = CertificateManager(tmp_path / "server")
    client_cm = CertificateManager(tmp_path / "client")
    return server_cm, client_cm


def _assert_backdated(
    cert: x509.Certificate,
    issued_before: datetime.datetime,
    issued_after: datetime.datetime,
    lifetime: datetime.timedelta,
):
    """notBefore is (issue_time - tolerance); the span stays == lifetime."""
    not_before = cert.not_valid_before_utc
    not_after = cert.not_valid_after_utc

    # notBefore sits one tolerance behind the moment of issuance, which we
    # only know to lie within [issued_before, issued_after].
    assert not_before >= issued_before - CLOCK_SKEW_TOLERANCE - _SLACK
    assert not_before <= issued_after - CLOCK_SKEW_TOLERANCE + _SLACK

    # It must be strictly in the past relative to the issuer's own clock,
    # which is the whole point (otherwise a co-located verifier is fine but
    # nothing is gained for a trailing one).
    assert not_before < issued_before

    # The window is shifted earlier, not widened: span == lifetime exactly.
    assert not_after - not_before == lifetime


def test_ca_not_before_backdated_by_tolerance(cert_managers):
    server_cm, _client_cm = cert_managers
    before = _now()
    assert server_cm.generate_ca(force=True)
    after = _now()
    _assert_backdated(_load(server_cm.ca_cert_path), before, after, _CA_LIFETIME)


def test_server_cert_not_before_backdated_by_tolerance(cert_managers):
    server_cm, _client_cm = cert_managers
    assert server_cm.generate_ca(force=True)
    before = _now()
    assert server_cm.generate_server_certificate(
        hostname="test.local", ip_addresses=["127.0.0.1"], force=True
    )
    after = _now()
    _assert_backdated(_load(server_cm.server_cert_path), before, after, _LEAF_LIFETIME)


def test_client_cert_not_before_backdated_by_tolerance(cert_managers):
    server_cm, client_cm = cert_managers
    assert server_cm.generate_ca(force=True)
    csr_pem = client_cm.generate_client_key_and_csr()
    before = _now()
    cert_pem = server_cm.sign_client_csr(csr_pem, "skew-uid")
    after = _now()
    assert cert_pem is not None
    cert = x509.load_pem_x509_certificate(cert_pem, default_backend())
    _assert_backdated(cert, before, after, _LEAF_LIFETIME)


def _valid_at(cert: x509.Certificate, when: datetime.datetime) -> bool:
    """Reproduce the TLS validity-interval check that gates the handshake.

    A peer emits "certificate is not yet valid" precisely when its own clock
    is below ``notBefore``; this mirrors that comparison against a simulated
    verifier clock without pulling in the full RFC 5280 chain profile (which
    also demands extensions unrelated to time).
    """
    return cert.not_valid_before_utc <= when <= cert.not_valid_after_utc


def test_cert_valid_when_verifier_clock_trails_issuer_within_tolerance(cert_managers):
    # A verifier whose clock is behind the issuer by up to the tolerance now
    # accepts the freshly-minted leaf. Before backdating this was the exact
    # case that raised "certificate is not yet valid".
    server_cm, client_cm = cert_managers
    assert server_cm.generate_ca(force=True)
    csr_pem = client_cm.generate_client_key_and_csr()
    issued_at = _now()
    cert_pem = server_cm.sign_client_csr(csr_pem, "trailing-clock-uid")
    assert cert_pem is not None
    leaf = x509.load_pem_x509_certificate(cert_pem, default_backend())

    # Clock trailing by almost the full tolerance: valid.
    trailing = issued_at - (CLOCK_SKEW_TOLERANCE - datetime.timedelta(minutes=1))
    assert _valid_at(leaf, trailing)
    # Right at the tolerance boundary: still valid.
    assert _valid_at(leaf, leaf.not_valid_before_utc)
    # A co-located verifier is trivially fine too.
    assert _valid_at(leaf, issued_at)


def test_cert_invalid_when_verifier_clock_trails_beyond_tolerance(cert_managers):
    # The tolerance is a bound, not a blank cheque: a clock further behind
    # than notBefore must still be treated as not-yet-valid.
    server_cm, client_cm = cert_managers
    assert server_cm.generate_ca(force=True)
    csr_pem = client_cm.generate_client_key_and_csr()
    cert_pem = server_cm.sign_client_csr(csr_pem, "way-behind-uid")
    assert cert_pem is not None
    leaf = x509.load_pem_x509_certificate(cert_pem, default_backend())

    too_early = leaf.not_valid_before_utc - datetime.timedelta(seconds=1)
    assert not _valid_at(leaf, too_early)
