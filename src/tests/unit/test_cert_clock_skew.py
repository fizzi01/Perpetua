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
import shutil
import tempfile
import unittest
from pathlib import Path

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


class TestCertificateClockSkew(unittest.TestCase):
    def setUp(self):
        self.server_dir = tempfile.mkdtemp()
        self.client_dir = tempfile.mkdtemp()
        self.server_cm = CertificateManager(Path(self.server_dir))
        self.client_cm = CertificateManager(Path(self.client_dir))

    def tearDown(self):
        shutil.rmtree(self.server_dir, ignore_errors=True)
        shutil.rmtree(self.client_dir, ignore_errors=True)

    def _assert_backdated(
        self,
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
        self.assertGreaterEqual(
            not_before, issued_before - CLOCK_SKEW_TOLERANCE - _SLACK
        )
        self.assertLessEqual(not_before, issued_after - CLOCK_SKEW_TOLERANCE + _SLACK)

        # It must be strictly in the past relative to the issuer's own clock,
        # which is the whole point (otherwise a co-located verifier is fine but
        # nothing is gained for a trailing one).
        self.assertLess(not_before, issued_before)

        # The window is shifted earlier, not widened: span == lifetime exactly.
        self.assertEqual(not_after - not_before, lifetime)

    def test_ca_not_before_backdated_by_tolerance(self):
        before = _now()
        self.assertTrue(self.server_cm.generate_ca(force=True))
        after = _now()
        self._assert_backdated(
            _load(self.server_cm.ca_cert_path), before, after, _CA_LIFETIME
        )

    def test_server_cert_not_before_backdated_by_tolerance(self):
        self.assertTrue(self.server_cm.generate_ca(force=True))
        before = _now()
        self.assertTrue(
            self.server_cm.generate_server_certificate(
                hostname="test.local", ip_addresses=["127.0.0.1"], force=True
            )
        )
        after = _now()
        self._assert_backdated(
            _load(self.server_cm.server_cert_path), before, after, _LEAF_LIFETIME
        )

    def test_client_cert_not_before_backdated_by_tolerance(self):
        self.assertTrue(self.server_cm.generate_ca(force=True))
        csr_pem = self.client_cm.generate_client_key_and_csr()
        before = _now()
        cert_pem = self.server_cm.sign_client_csr(csr_pem, "skew-uid")
        after = _now()
        self.assertIsNotNone(cert_pem)
        cert = x509.load_pem_x509_certificate(cert_pem, default_backend())
        self._assert_backdated(cert, before, after, _LEAF_LIFETIME)

    @staticmethod
    def _valid_at(cert: x509.Certificate, when: datetime.datetime) -> bool:
        """Reproduce the TLS validity-interval check that gates the handshake.

        A peer emits "certificate is not yet valid" precisely when its own clock
        is below ``notBefore``; this mirrors that comparison against a simulated
        verifier clock without pulling in the full RFC 5280 chain profile (which
        also demands extensions unrelated to time).
        """
        return cert.not_valid_before_utc <= when <= cert.not_valid_after_utc

    def test_cert_valid_when_verifier_clock_trails_issuer_within_tolerance(self):
        # A verifier whose clock is behind the issuer by up to the tolerance now
        # accepts the freshly-minted leaf. Before backdating this was the exact
        # case that raised "certificate is not yet valid".
        self.assertTrue(self.server_cm.generate_ca(force=True))
        csr_pem = self.client_cm.generate_client_key_and_csr()
        issued_at = _now()
        cert_pem = self.server_cm.sign_client_csr(csr_pem, "trailing-clock-uid")
        self.assertIsNotNone(cert_pem)
        leaf = x509.load_pem_x509_certificate(cert_pem, default_backend())

        # Clock trailing by almost the full tolerance: valid.
        trailing = issued_at - (CLOCK_SKEW_TOLERANCE - datetime.timedelta(minutes=1))
        self.assertTrue(self._valid_at(leaf, trailing))
        # Right at the tolerance boundary: still valid.
        self.assertTrue(self._valid_at(leaf, leaf.not_valid_before_utc))
        # A co-located verifier is trivially fine too.
        self.assertTrue(self._valid_at(leaf, issued_at))

    def test_cert_invalid_when_verifier_clock_trails_beyond_tolerance(self):
        # The tolerance is a bound, not a blank cheque: a clock further behind
        # than notBefore must still be treated as not-yet-valid.
        self.assertTrue(self.server_cm.generate_ca(force=True))
        csr_pem = self.client_cm.generate_client_key_and_csr()
        cert_pem = self.server_cm.sign_client_csr(csr_pem, "way-behind-uid")
        self.assertIsNotNone(cert_pem)
        leaf = x509.load_pem_x509_certificate(cert_pem, default_backend())

        too_early = leaf.not_valid_before_utc - datetime.timedelta(seconds=1)
        self.assertFalse(self._valid_at(leaf, too_early))


if __name__ == "__main__":
    unittest.main()
