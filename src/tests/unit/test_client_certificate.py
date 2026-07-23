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

import pytest
from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.x509.oid import NameOID

from utils.crypto import CertificateManager
from network.connection.server import ConnectionHandler


# ---------------------------------------------------------------------------
# Client certificate issuance
# ---------------------------------------------------------------------------
@pytest.fixture
def issuance_setup(tmp_path):
    """A server CA (+ server cert) and a separate client cert store."""
    server_dir = tmp_path / "server"
    client_dir = tmp_path / "client"
    server_dir.mkdir()
    client_dir.mkdir()

    server_cm = CertificateManager(server_dir)
    client_cm = CertificateManager(client_dir)
    server_cm.generate_ca()
    server_cm.generate_server_certificate(
        ip_addresses=["127.0.0.1"], hostname="test.local"
    )
    return server_cm, client_cm


def _load_ca(server_cm):
    with open(server_cm.ca_cert_path, "rb") as f:
        return x509.load_pem_x509_certificate(f.read(), default_backend())


def test_csr_has_placeholder_cn_not_a_real_uid(issuance_setup):
    # The client does not choose a UID: the CSR carries a placeholder CN so
    # nothing on the plaintext pairing channel reveals a real UID.
    _server_cm, client_cm = issuance_setup
    csr_pem = client_cm.generate_client_key_and_csr()
    assert csr_pem is not None
    assert client_cm.client_key_path.exists()
    csr = x509.load_pem_x509_csr(csr_pem, default_backend())
    cn = csr.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value
    assert cn == CertificateManager.CLIENT_CSR_PLACEHOLDER_CN


def test_server_stamps_assigned_uid_ignoring_csr_cn(issuance_setup):
    # Whatever CN the CSR carries, the server-supplied uid wins.
    server_cm, client_cm = issuance_setup
    uid = "server-assigned-uid-123"
    csr_pem = client_cm.generate_client_key_and_csr()
    cert_pem = server_cm.sign_client_csr(csr_pem, uid)
    assert cert_pem is not None
    cert = x509.load_pem_x509_certificate(cert_pem, default_backend())
    cn = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value
    assert cn == uid
    assert cert.issuer == _load_ca(server_cm).subject


def test_read_certificate_common_name(issuance_setup):
    server_cm, client_cm = issuance_setup
    uid = "cn-read-uid"
    csr_pem = client_cm.generate_client_key_and_csr()
    cert_pem = server_cm.sign_client_csr(csr_pem, uid)
    # The client learns its UID by reading the CN of its issued cert.
    assert CertificateManager.read_certificate_common_name(cert_pem) == uid
    assert CertificateManager.read_certificate_common_name(b"not a cert") is None


def test_get_client_uid_returns_cn_of_installed_cert(issuance_setup):
    # The client UID's single source of truth is the installed client.crt.
    server_cm, client_cm = issuance_setup
    assert client_cm.get_client_uid() is None
    uid = "installed-cert-uid"
    csr_pem = client_cm.generate_client_key_and_csr()
    cert_pem = server_cm.sign_client_csr(csr_pem, uid)
    assert client_cm.save_client_certificate(cert_pem)
    assert client_cm.get_client_uid() == uid
    # Removing the credentials drops the derived identity again.
    client_cm.remove_client_credentials()
    assert client_cm.get_client_uid() is None


def test_garbage_csr_rejected(issuance_setup):
    server_cm, _client_cm = issuance_setup
    assert server_cm.sign_client_csr(b"not a csr", "uid") is None


def test_client_credentials_roundtrip(issuance_setup):
    server_cm, client_cm = issuance_setup
    assert not client_cm.client_credentials_exist()
    csr_pem = client_cm.generate_client_key_and_csr()
    cert_pem = server_cm.sign_client_csr(csr_pem, "uid-x")
    assert client_cm.save_client_certificate(cert_pem)
    assert client_cm.client_credentials_exist()
    cert, key = client_cm.get_client_credentials()
    assert cert is not None
    assert key is not None
    # Removal (used before re-pairing) clears both.
    assert client_cm.remove_client_credentials()
    assert not client_cm.client_credentials_exist()


def test_security_info_reports_valid_certificate_material(issuance_setup):
    server_cm, client_cm = issuance_setup
    csr_pem = client_cm.generate_client_key_and_csr()
    cert_pem = server_cm.sign_client_csr(csr_pem, "secure-client-uid")
    assert client_cm.save_client_certificate(cert_pem)
    with open(server_cm.ca_cert_path, "rb") as f:
        assert client_cm.save_ca_data(f.read(), "server-uid")

    info = client_cm.get_security_info("server-uid", ssl_enabled=True)

    assert info["mutual_tls_available"]
    assert info["private_key_present"]
    assert info["server_ca"]["present"]
    assert info["client_certificate"]["present"]
    assert info["client_certificate"]["subject_common_name"] == "secure-client-uid"
    assert info["server_ca"]["public_key_algorithm"] == "RSA"
    assert info["server_ca"]["public_key_size"] == 4096
    assert info["client_certificate"]["public_key_size"] == 2048
    assert len(info["server_ca"]["sha256_fingerprint"]) == 64
    assert len(info["client_certificate"]["sha256_fingerprint"]) == 64
    assert "path" not in info["server_ca"]
    assert "path" not in info["client_certificate"]


def test_security_info_reports_missing_private_key(issuance_setup):
    server_cm, client_cm = issuance_setup
    csr_pem = client_cm.generate_client_key_and_csr()
    cert_pem = server_cm.sign_client_csr(csr_pem, "missing-key-uid")
    assert client_cm.save_client_certificate(cert_pem)
    with open(server_cm.ca_cert_path, "rb") as f:
        assert client_cm.save_ca_data(f.read(), "server-uid")
    client_cm.client_key_path.unlink()

    info = client_cm.get_security_info("server-uid", ssl_enabled=True)

    assert not info["mutual_tls_available"]
    assert not info["private_key_present"]
    assert info["client_certificate"]["present"]


def test_security_info_handles_unreadable_certificate(issuance_setup):
    _server_cm, client_cm = issuance_setup
    bad_cert = client_cm.cert_dir / "bad.crt"
    bad_cert.write_bytes(b"not a certificate")
    assert client_cm.extend_mapping("bad-server", bad_cert.name)

    info = client_cm.get_security_info("bad-server", ssl_enabled=True)

    assert not info["mutual_tls_available"]
    assert not info["server_ca"]["present"]
    assert info["server_ca"]["error"] == "unreadable"


# ---------------------------------------------------------------------------
# Server mutual-TLS context
# ---------------------------------------------------------------------------
@pytest.fixture
def server_tls_setup(tmp_path):
    """A server CertificateManager with a minted CA + server certificate."""
    server_dir = tmp_path / "server"
    server_dir.mkdir()
    cm = CertificateManager(server_dir)
    cm.generate_ca()
    cm.generate_server_certificate(ip_addresses=["127.0.0.1"], hostname="test.local")
    certfile, keyfile = cm.get_server_credentials()
    return cm, certfile, keyfile


def test_context_requires_client_cert_when_ca_present(server_tls_setup):
    cm, certfile, keyfile = server_tls_setup
    handler = ConnectionHandler(
        certfile=certfile,
        keyfile=keyfile,
        ca_certfile=cm.get_ca_cert_path(),
        ssl_enabled=True,
    )
    ctx = handler._get_ssl_context()
    assert ctx is not None
    assert ctx.verify_mode == ssl.CERT_REQUIRED


def test_context_without_ca_does_not_require_client_cert(server_tls_setup):
    _cm, certfile, keyfile = server_tls_setup
    handler = ConnectionHandler(
        certfile=certfile,
        keyfile=keyfile,
        ca_certfile=None,
        ssl_enabled=True,
    )
    ctx = handler._get_ssl_context()
    assert ctx is not None
    assert ctx.verify_mode == ssl.CERT_NONE


# ---------------------------------------------------------------------------
# Peer certificate CN extraction
# ---------------------------------------------------------------------------
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


def test_extracts_common_name():
    peercert = {"subject": ((("commonName", "my-uid"),),)}
    writer = _FakeWriter(_FakeSSLObject(peercert))
    assert ConnectionHandler._peer_cert_cn(writer) == "my-uid"


def test_none_without_tls():
    writer = _FakeWriter(None)
    assert ConnectionHandler._peer_cert_cn(writer) is None


def test_none_when_no_cn():
    peercert = {"subject": ((("organizationName", "Perpetua"),),)}
    writer = _FakeWriter(_FakeSSLObject(peercert))
    assert ConnectionHandler._peer_cert_cn(writer) is None
