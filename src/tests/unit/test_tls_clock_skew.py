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

"""Tests for clock-skew tolerance at the TLS handshake layer.

A peer whose clock trails the certificate issuer used to fail every connection
with "certificate is not yet valid". The connection handlers now disable
OpenSSL's built-in validity-window check (:func:`apply_skew_tolerant_time_policy`)
while re-enforcing expiry only (:func:`peer_cert_is_expired`). These tests drive
a real localhost TLS handshake to prove both halves.
"""

import datetime
import socket
import ssl
import threading
import unittest

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from network.connection.handler import (
    SSL_NO_CHECK_TIME,
    apply_skew_tolerant_time_policy,
    peer_cert_is_expired,
)


def _mkcert(not_before, not_after, ca_key=None, ca_cert=None, is_ca=False, cn="localhost"):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    issuer = ca_cert.subject if ca_cert else subject
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
    )
    if is_ca:
        builder = builder.add_extension(
            x509.BasicConstraints(ca=True, path_length=None), critical=True
        )
    else:
        builder = builder.add_extension(
            x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False
        )
    cert = builder.sign(ca_key or key, hashes.SHA256())
    return cert, key


def _pem(cert):
    return cert.public_bytes(serialization.Encoding.PEM)


def _key_pem(key):
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )


class _TlsHarness:
    """Run one localhost TLS handshake and report success/failure per side.

    The server presents ``server_cert``/``server_key`` signed by ``ca_cert``;
    the client verifies against ``ca_cert`` with the skew-tolerant time policy
    applied. Returns the client-side handshake result.
    """

    def __init__(self, tmpdir, ca_cert, server_cert, server_key):
        self.ca_p = tmpdir / "ca.pem"
        self.srv_p = tmpdir / "srv.pem"
        self.key_p = tmpdir / "srv.key"
        self.ca_p.write_bytes(_pem(ca_cert))
        self.srv_p.write_bytes(_pem(server_cert))
        self.key_p.write_bytes(_key_pem(server_key))

    def run(self, apply_policy: bool):
        sctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        sctx.load_cert_chain(str(self.srv_p), str(self.key_p))

        cctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
        cctx.load_verify_locations(str(self.ca_p))
        if apply_policy:
            apply_skew_tolerant_time_policy(cctx)

        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]

        def serve():
            try:
                raw, _ = listener.accept()
                with sctx.wrap_socket(raw, server_side=True) as s:
                    s.recv(16)
            except Exception:
                pass

        t = threading.Thread(target=serve)
        t.start()
        try:
            with socket.create_connection(("127.0.0.1", port)) as raw:
                with cctx.wrap_socket(raw, server_hostname="localhost") as s:
                    s.send(b"hi")
            return None
        except ssl.SSLError as e:
            return e
        finally:
            t.join()
            listener.close()


class TestTlsClockSkewTolerance(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path

        self._dir = tempfile.mkdtemp()
        self.tmp = Path(self._dir)
        now = datetime.datetime.now(datetime.UTC)
        self.ca_cert, self.ca_key = _mkcert(
            now - datetime.timedelta(days=1),
            now + datetime.timedelta(days=3650),
            is_ca=True,
            cn="TestCA",
        )
        self._now = now

    def tearDown(self):
        import shutil

        shutil.rmtree(self._dir, ignore_errors=True)

    def test_flag_constant_matches_openssl(self):
        self.assertEqual(SSL_NO_CHECK_TIME, 0x200000)

    def test_policy_sets_the_flag(self):
        ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
        apply_skew_tolerant_time_policy(ctx)
        self.assertTrue(int(ctx.verify_flags) & SSL_NO_CHECK_TIME)

    def test_not_yet_valid_cert_rejected_without_policy(self):
        # Baseline: reproduce the user's failure. A server cert that is not yet
        # valid (issued "tomorrow") is rejected when the policy is NOT applied.
        future = self._now + datetime.timedelta(days=1)
        srv, key = _mkcert(
            future,
            future + datetime.timedelta(days=365),
            ca_key=self.ca_key,
            ca_cert=self.ca_cert,
        )
        err = _TlsHarness(self.tmp, self.ca_cert, srv, key).run(apply_policy=False)
        self.assertIsNotNone(err)
        self.assertIn("not yet valid", str(err).lower())

    def test_not_yet_valid_cert_accepted_with_policy(self):
        # With the skew-tolerant policy, the same not-yet-valid cert handshakes
        # successfully — a client behind by a day can now connect.
        future = self._now + datetime.timedelta(days=1)
        srv, key = _mkcert(
            future,
            future + datetime.timedelta(days=365),
            ca_key=self.ca_key,
            ca_cert=self.ca_cert,
        )
        err = _TlsHarness(self.tmp, self.ca_cert, srv, key).run(apply_policy=True)
        self.assertIsNone(err, f"handshake should have succeeded, got: {err}")

    def test_untrusted_ca_still_rejected_with_policy(self):
        # The policy relaxes ONLY time: a cert from an unknown CA is still
        # rejected, proving chain/signature verification is intact.
        other_ca, other_key = _mkcert(
            self._now - datetime.timedelta(days=1),
            self._now + datetime.timedelta(days=3650),
            is_ca=True,
            cn="OtherCA",
        )
        srv, key = _mkcert(
            self._now,
            self._now + datetime.timedelta(days=365),
            ca_key=other_key,
            ca_cert=other_ca,
        )
        # Client trusts self.ca_cert, but the server cert is signed by other_ca.
        err = _TlsHarness(self.tmp, self.ca_cert, srv, key).run(apply_policy=True)
        self.assertIsNotNone(err)
        self.assertIn("verify failed", str(err).lower())


class TestPeerCertExpiry(unittest.TestCase):
    """The manual expiry re-check that keeps notAfter enforced."""

    class _FakeSSLObject:
        def __init__(self, peercert):
            self._peercert = peercert

        def getpeercert(self):
            return self._peercert

    @staticmethod
    def _cert_dict(not_after: datetime.datetime):
        # ssl.getpeercert() renders notAfter in this exact strftime layout.
        stamp = not_after.strftime("%b %d %H:%M:%S %Y GMT")
        return {"notAfter": stamp, "subject": ((("commonName", "peer"),),)}

    def test_expired_cert_detected(self):
        past = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=1)
        obj = self._FakeSSLObject(self._cert_dict(past))
        self.assertTrue(peer_cert_is_expired(obj))

    def test_valid_cert_not_flagged(self):
        future = datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=30)
        obj = self._FakeSSLObject(self._cert_dict(future))
        self.assertFalse(peer_cert_is_expired(obj))

    def test_no_tls_layer_is_not_expired(self):
        self.assertFalse(peer_cert_is_expired(None))

    def test_no_peer_cert_is_not_expired(self):
        self.assertFalse(peer_cert_is_expired(self._FakeSSLObject(None)))

    def test_missing_not_after_is_not_expired(self):
        obj = self._FakeSSLObject({"subject": ((("commonName", "peer"),),)})
        self.assertFalse(peer_cert_is_expired(obj))


if __name__ == "__main__":
    unittest.main()
