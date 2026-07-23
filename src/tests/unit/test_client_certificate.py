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

"""Tests for per-client certificate issuance and the mutual-TLS identity binding.

These cover the anti-UID-theft mechanism: the server signs a client CSR into a
CA-verifiable leaf cert whose Common Name it forces to the client UID, and the
server connection handler binds that verified cert's CN to the UID claimed in
the handshake.
"""

import ssl
import tempfile
import shutil
import unittest
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.x509.oid import NameOID

from utils.crypto import CertificateManager
from network.connection.server import ConnectionHandler


class TestClientCertificateIssuance(unittest.TestCase):
    def setUp(self):
        self.server_dir = tempfile.mkdtemp()
        self.client_dir = tempfile.mkdtemp()
        self.server_cm = CertificateManager(Path(self.server_dir))
        self.client_cm = CertificateManager(Path(self.client_dir))
        self.server_cm.generate_ca()
        self.server_cm.generate_server_certificate(
            ip_addresses=["127.0.0.1"], hostname="test.local"
        )

    def tearDown(self):
        shutil.rmtree(self.server_dir, ignore_errors=True)
        shutil.rmtree(self.client_dir, ignore_errors=True)

    def _load_ca(self):
        with open(self.server_cm.ca_cert_path, "rb") as f:
            return x509.load_pem_x509_certificate(f.read(), default_backend())

    def test_csr_has_placeholder_cn_not_a_real_uid(self):
        # The client does not choose a UID: the CSR carries a placeholder CN so
        # nothing on the plaintext pairing channel reveals a real UID.
        csr_pem = self.client_cm.generate_client_key_and_csr()
        self.assertIsNotNone(csr_pem)
        self.assertTrue(self.client_cm.client_key_path.exists())
        csr = x509.load_pem_x509_csr(csr_pem, default_backend())
        cn = csr.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value
        self.assertEqual(cn, CertificateManager.CLIENT_CSR_PLACEHOLDER_CN)

    def test_server_stamps_assigned_uid_ignoring_csr_cn(self):
        # Whatever CN the CSR carries, the server-supplied uid wins.
        uid = "server-assigned-uid-123"
        csr_pem = self.client_cm.generate_client_key_and_csr()
        cert_pem = self.server_cm.sign_client_csr(csr_pem, uid)
        self.assertIsNotNone(cert_pem)
        cert = x509.load_pem_x509_certificate(cert_pem, default_backend())
        cn = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value
        self.assertEqual(cn, uid)
        self.assertEqual(cert.issuer, self._load_ca().subject)

    def test_read_certificate_common_name(self):
        uid = "cn-read-uid"
        csr_pem = self.client_cm.generate_client_key_and_csr()
        cert_pem = self.server_cm.sign_client_csr(csr_pem, uid)
        # The client learns its UID by reading the CN of its issued cert.
        self.assertEqual(CertificateManager.read_certificate_common_name(cert_pem), uid)
        self.assertIsNone(
            CertificateManager.read_certificate_common_name(b"not a cert")
        )

    def test_get_client_uid_returns_cn_of_installed_cert(self):
        # The client UID's single source of truth is the installed client.crt.
        self.assertIsNone(self.client_cm.get_client_uid())
        uid = "installed-cert-uid"
        csr_pem = self.client_cm.generate_client_key_and_csr()
        cert_pem = self.server_cm.sign_client_csr(csr_pem, uid)
        self.assertTrue(self.client_cm.save_client_certificate(cert_pem))
        self.assertEqual(self.client_cm.get_client_uid(), uid)
        # Removing the credentials drops the derived identity again.
        self.client_cm.remove_client_credentials()
        self.assertIsNone(self.client_cm.get_client_uid())

    def test_garbage_csr_rejected(self):
        self.assertIsNone(self.server_cm.sign_client_csr(b"not a csr", "uid"))

    def test_client_credentials_roundtrip(self):
        self.assertFalse(self.client_cm.client_credentials_exist())
        csr_pem = self.client_cm.generate_client_key_and_csr()
        cert_pem = self.server_cm.sign_client_csr(csr_pem, "uid-x")
        self.assertTrue(self.client_cm.save_client_certificate(cert_pem))
        self.assertTrue(self.client_cm.client_credentials_exist())
        cert, key = self.client_cm.get_client_credentials()
        self.assertIsNotNone(cert)
        self.assertIsNotNone(key)
        # Removal (used before re-pairing) clears both.
        self.assertTrue(self.client_cm.remove_client_credentials())
        self.assertFalse(self.client_cm.client_credentials_exist())


class TestServerMutualTlsContext(unittest.TestCase):
    def setUp(self):
        self.server_dir = tempfile.mkdtemp()
        self.cm = CertificateManager(Path(self.server_dir))
        self.cm.generate_ca()
        self.cm.generate_server_certificate(
            ip_addresses=["127.0.0.1"], hostname="test.local"
        )
        cert, key = self.cm.get_server_credentials()
        self.certfile, self.keyfile = cert, key

    def tearDown(self):
        shutil.rmtree(self.server_dir, ignore_errors=True)

    def test_context_requires_client_cert_when_ca_present(self):
        handler = ConnectionHandler(
            certfile=self.certfile,
            keyfile=self.keyfile,
            ca_certfile=self.cm.get_ca_cert_path(),
            ssl_enabled=True,
        )
        ctx = handler._get_ssl_context()
        self.assertIsNotNone(ctx)
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)

    def test_context_without_ca_does_not_require_client_cert(self):
        handler = ConnectionHandler(
            certfile=self.certfile,
            keyfile=self.keyfile,
            ca_certfile=None,
            ssl_enabled=True,
        )
        ctx = handler._get_ssl_context()
        self.assertIsNotNone(ctx)
        self.assertEqual(ctx.verify_mode, ssl.CERT_NONE)


class _FakeSSLObject:
    def __init__(self, peercert):
        self._peercert = peercert

    def getpeercert(self):
        return self._peercert


class _FakeWriter:
    def __init__(self, ssl_object):
        self._ssl_object = ssl_object

    def get_extra_info(self, key):
        if key == "ssl_object":
            return self._ssl_object
        return None


class TestPeerCertCnExtraction(unittest.TestCase):
    def test_extracts_common_name(self):
        peercert = {"subject": ((("commonName", "my-uid"),),)}
        writer = _FakeWriter(_FakeSSLObject(peercert))
        self.assertEqual(ConnectionHandler._peer_cert_cn(writer), "my-uid")

    def test_none_without_tls(self):
        writer = _FakeWriter(None)
        self.assertIsNone(ConnectionHandler._peer_cert_cn(writer))

    def test_none_when_no_cn(self):
        peercert = {"subject": ((("organizationName", "Perpetua"),),)}
        writer = _FakeWriter(_FakeSSLObject(peercert))
        self.assertIsNone(ConnectionHandler._peer_cert_cn(writer))


if __name__ == "__main__":
    unittest.main()
