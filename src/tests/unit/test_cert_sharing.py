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

# tests/test_cert_sharing.py
"""
Unit tests for certificate sharing system with OTP-derived AES-GCM encryption
"""

import asyncio
import os
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from utils.crypto.sharing import CertificateSharing, CertificateReceiver
from utils.crypto import CertificateManager


# ---------------------------------------------------------------------------
# CertificateSharing (was TestCertificateSharing)
# ---------------------------------------------------------------------------
@pytest.fixture
def sharing_ctx(tmp_path):
    """Test fixtures for the CertificateSharing suite."""
    cert_manager = CertificateManager(tmp_path)

    # Generate test certificates
    cert_manager.generate_ca()
    cert_manager.generate_server_certificate(
        ip_addresses=["127.0.0.1"], hostname="test.local"
    )

    # Load CA certificate data
    ca_cert_path = cert_manager.get_ca_cert_path()
    if ca_cert_path is None:
        pytest.fail("CA certificate path not found")

    with open(file=ca_cert_path, mode="rb") as f:  # ty:ignore[no-matching-overload]
        cert_data = f.read()

    return SimpleNamespace(
        cert_manager=cert_manager,
        cert_data=cert_data,
        test_host="127.0.0.1",
        test_port=15556,  # Use non-standard port for testing
        test_timeout=5,
    )


def test_otp_generation():
    """Test OTP generation is secure and correct length"""
    otp = CertificateSharing._generate_otp()

    # Check OTP is 6 digits
    assert len(otp) == 6

    # Check OTP contains only digits
    assert otp.isdigit()

    # Check multiple OTPs are different (statistical test)
    otps = [CertificateSharing._generate_otp() for _ in range(100)]
    unique_otps = set(otps)
    assert len(unique_otps) > 90  # At least 90% unique


def test_envelope_creation_and_decryption(sharing_ctx):
    """The certificate envelope is base64(JSON) of AES-GCM fields (no JWT)."""
    import base64
    import json

    sharing = CertificateSharing(
        cert_data=sharing_ctx.cert_data,
        host=sharing_ctx.test_host,
        port=sharing_ctx.test_port,
        timeout=sharing_ctx.test_timeout,
    )

    otp = "123456"
    token = asyncio.run(sharing._create_envelope(otp))

    # Token should be a non-empty string
    assert isinstance(token, str)
    assert len(token) > 0

    # It decodes as base64(JSON) - NOT a JWT (no dotted header.payload.sig).
    payload = json.loads(base64.b64decode(token))

    # Check payload carries the AES-GCM fields; no signature / exp / iat.
    assert "encrypted_cert" in payload
    assert "nonce" in payload
    assert "salt" in payload
    assert "exp" not in payload
    assert "iat" not in payload

    # Check salt is base64 encoded string
    assert isinstance(payload["salt"], str)
    assert len(payload["salt"]) > 0

    # Decrypt certificate data using salt from payload
    decrypted_cert = CertificateSharing.decrypt_data(
        encrypted_data=base64.b64decode(payload["encrypted_cert"]),
        nonce=base64.b64decode(payload["nonce"]),
        salt=base64.b64decode(payload["salt"]),
        otp=otp,
    )

    # Check decrypted certificate matches original
    cert_str = (
        sharing_ctx.cert_data.decode("utf-8")
        if isinstance(sharing_ctx.cert_data, bytes)
        else sharing_ctx.cert_data
    )
    # Remove any leading/trailing whitespace for comparison
    cert_str_normalized = cert_str.replace("\r\n", "\n").replace("\r", "\n")
    decrypted_cert_normalized = (
        decrypted_cert.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    )
    assert decrypted_cert_normalized == cert_str_normalized


def test_otp_expiry(sharing_ctx):
    """Test OTP expiry mechanism"""
    sharing = CertificateSharing(
        cert_data=sharing_ctx.cert_data,
        host=sharing_ctx.test_host,
        port=sharing_ctx.test_port,
        timeout=1,  # 1 second timeout
    )

    # Initially no OTP
    assert not sharing._is_otp_valid()

    # Generate OTP
    sharing._otp = "123456"
    sharing._otp_expiry = time.time() + 1

    # Should be valid
    assert sharing._is_otp_valid()

    # Wait for expiry
    time.sleep(1.1)

    # Should be expired
    assert not sharing._is_otp_valid()


@pytest.mark.anyio
async def test_start_sharing_success(sharing_ctx):
    """Test successful sharing server start"""
    sharing = CertificateSharing(
        cert_data=sharing_ctx.cert_data,
        host=sharing_ctx.test_host,
        port=sharing_ctx.test_port,
        timeout=sharing_ctx.test_timeout,
    )

    success, otp = await sharing.start_sharing()

    try:
        # Check success
        assert success
        assert otp is not None
        assert len(otp) == 6  # ty:ignore[invalid-argument-type]
        assert otp.isdigit()  # ty:ignore[possibly-missing-attribute]

        # Check server is running
        assert sharing.is_sharing_active()

        # Check OTP can be retrieved
        retrieved_otp = sharing.get_otp()
        assert retrieved_otp == otp

    finally:
        await sharing.stop_sharing()


@pytest.mark.anyio
async def test_start_sharing_twice(sharing_ctx):
    """Test starting sharing when already active"""
    sharing = CertificateSharing(
        cert_data=sharing_ctx.cert_data,
        host=sharing_ctx.test_host,
        port=sharing_ctx.test_port,
        timeout=sharing_ctx.test_timeout,
    )

    # Start first time
    success1, otp1 = await sharing.start_sharing()
    assert success1

    # Try to start second time
    success2, otp2 = await sharing.start_sharing()
    assert not success2
    assert otp2 is None

    await sharing.stop_sharing()


@pytest.mark.anyio
async def test_stop_sharing(sharing_ctx):
    """Test stopping sharing server"""
    sharing = CertificateSharing(
        cert_data=sharing_ctx.cert_data,
        host=sharing_ctx.test_host,
        port=sharing_ctx.test_port,
        timeout=sharing_ctx.test_timeout,
    )

    # Start sharing
    await sharing.start_sharing()
    assert sharing.is_sharing_active()

    # Stop sharing
    await sharing.stop_sharing()
    assert not sharing.is_sharing_active()

    # OTP should be invalidated
    assert sharing.get_otp() is None


@pytest.mark.anyio
async def test_auto_shutdown(sharing_ctx):
    """Test automatic shutdown after timeout"""
    sharing = CertificateSharing(
        cert_data=sharing_ctx.cert_data,
        host=sharing_ctx.test_host,
        port=sharing_ctx.test_port,
        timeout=2,  # 2 seconds
    )

    await sharing.start_sharing()
    assert sharing.is_sharing_active()

    # Wait for auto shutdown
    await asyncio.sleep(2.5)

    # Should be stopped
    assert not sharing.is_sharing_active()


@pytest.mark.anyio
async def test_full_sharing_workflow(sharing_ctx):
    """Test complete sharing workflow from server to client"""
    # Start sharing server
    sharing = CertificateSharing(
        cert_data=sharing_ctx.cert_data,
        host=sharing_ctx.test_host,
        port=sharing_ctx.test_port,
        timeout=10,
    )

    success, otp = await sharing.start_sharing()
    assert success
    assert otp is not None

    try:
        # Give server time to start
        await asyncio.sleep(0.5)

        # Connect as client
        receiver = CertificateReceiver(
            server_host=sharing_ctx.test_host,
            server_port=sharing_ctx.test_port,
            timeout=5,
        )

        success, received_cert, _ = await receiver.receive_certificate(otp)  # ty:ignore[invalid-argument-type]

        # Check success
        assert success
        assert received_cert is not None

        # Check certificate matches original
        original_cert = sharing_ctx.cert_data.decode("utf-8")
        assert received_cert == original_cert

        # Check sharing was marked as successful
        assert sharing.was_shared()

    finally:
        await sharing.stop_sharing()


@pytest.mark.anyio
async def test_receive_with_wrong_otp(sharing_ctx):
    """Test certificate reception with wrong OTP"""
    # Start sharing server
    sharing = CertificateSharing(
        cert_data=sharing_ctx.cert_data,
        host=sharing_ctx.test_host,
        port=sharing_ctx.test_port,
        timeout=10,
    )

    success, correct_otp = await sharing.start_sharing()
    assert success

    try:
        await asyncio.sleep(0.5)

        # Try to receive with wrong OTP
        receiver = CertificateReceiver(
            server_host=sharing_ctx.test_host,
            server_port=sharing_ctx.test_port,
            timeout=5,
        )

        wrong_otp = "000000"  # Wrong OTP
        success, received_cert, _ = await receiver.receive_certificate(wrong_otp)

        # Should fail
        assert not success
        assert received_cert is None

    finally:
        await sharing.stop_sharing()


@pytest.mark.anyio
async def test_receive_after_expiry(sharing_ctx):
    """Test certificate reception after OTP expiry"""
    # Start sharing server with short timeout
    sharing = CertificateSharing(
        cert_data=sharing_ctx.cert_data,
        host=sharing_ctx.test_host,
        port=sharing_ctx.test_port,
        timeout=1,  # 1 second
    )

    success, otp = await sharing.start_sharing()
    assert success
    assert otp is not None

    try:
        # Wait for OTP to expire
        await asyncio.sleep(1.5)

        # Try to receive after expiry
        receiver = CertificateReceiver(
            server_host=sharing_ctx.test_host,
            server_port=sharing_ctx.test_port,
            timeout=5,
        )

        success, received_cert, _ = await receiver.receive_certificate(otp)

        # Should fail (server auto-shutdown)
        assert not success
        assert received_cert is None

    finally:
        # Try to stop (should already be stopped)
        await sharing.stop_sharing()


@pytest.mark.anyio
async def test_receive_connection_refused(sharing_ctx):
    """Test certificate reception when server is not running"""
    receiver = CertificateReceiver(
        server_host=sharing_ctx.test_host,
        server_port=sharing_ctx.test_port,
        timeout=2,
    )

    # Try to receive without server running
    success, received_cert, _ = await receiver.receive_certificate("123456")

    # Should fail
    assert not success
    assert received_cert is None


@pytest.mark.anyio
async def test_otp_is_single_use(sharing_ctx):
    """An OTP is consumed on first successful delivery; a replay fails.

    This is the anti-replay guarantee: a passive eavesdropper can only
    capture the token after the legitimate client received it, by which
    point the OTP is already burned.
    """
    sharing = CertificateSharing(
        cert_data=sharing_ctx.cert_data,
        host=sharing_ctx.test_host,
        port=sharing_ctx.test_port,
        timeout=10,
    )

    success, otp = await sharing.start_sharing()
    assert success

    try:
        await asyncio.sleep(0.3)

        # First client succeeds and consumes the OTP.
        first = CertificateReceiver(
            server_host=sharing_ctx.test_host,
            server_port=sharing_ctx.test_port,
            timeout=5,
        )
        ok1, cert1, _ = await first.receive_certificate(otp)
        assert ok1
        assert cert1 is not None

        # A replay with the same (now consumed) OTP is rejected.
        replay = CertificateReceiver(
            server_host=sharing_ctx.test_host,
            server_port=sharing_ctx.test_port,
            timeout=5,
        )
        ok2, cert2, _ = await replay.receive_certificate(otp)
        assert not ok2
        assert cert2 is None

    finally:
        await sharing.stop_sharing()


@pytest.mark.anyio
async def test_receiver_timeout(sharing_ctx):
    """Test receiver timeout when server doesn't respond"""

    # Start server but don't send anything
    async def dummy_handler(reader, writer):
        # Just wait and do nothing
        await asyncio.sleep(10)
        writer.close()

    server = await asyncio.start_server(
        dummy_handler, sharing_ctx.test_host, sharing_ctx.test_port
    )

    try:
        receiver = CertificateReceiver(
            server_host=sharing_ctx.test_host,
            server_port=sharing_ctx.test_port,
            timeout=1,  # Short timeout
        )

        success, cert, _ = await receiver.receive_certificate("123456")

        # Should timeout and fail
        assert not success
        assert cert is None

    finally:
        server.close()
        await server.wait_closed()


# ---------------------------------------------------------------------------
# Integration (was TestCertificateSharingIntegration)
# ---------------------------------------------------------------------------
@pytest.fixture
def integration_ctx(tmp_path):
    """Test fixtures for the integration suite."""
    cert_manager = CertificateManager(tmp_path)
    cert_manager.generate_ca()
    cert_manager.generate_server_certificate(
        ip_addresses=["127.0.0.1"], hostname="test.local"
    )

    return SimpleNamespace(
        temp_dir=str(tmp_path),
        cert_manager=cert_manager,
        test_host="127.0.0.1",
        test_port=15557,
        test_timeout=10,
    )


@pytest.mark.anyio
async def test_complete_workflow_with_file_save(integration_ctx):
    """Test complete workflow including saving received certificate"""
    # Load certificate
    ca_cert_path = integration_ctx.cert_manager.get_ca_cert_path()
    with open(ca_cert_path, "rb") as f:
        cert_data = f.read()

    # Start sharing
    sharing = CertificateSharing(
        cert_data=cert_data,
        host=integration_ctx.test_host,
        port=integration_ctx.test_port,
        timeout=integration_ctx.test_timeout,
    )

    success, otp = await sharing.start_sharing()
    assert success

    try:
        await asyncio.sleep(0.5)

        # Receive certificate
        receiver = CertificateReceiver(
            server_host=integration_ctx.test_host,
            server_port=integration_ctx.test_port,
            timeout=5,
        )

        success, received_cert, _ = await receiver.receive_certificate(otp)
        assert success

        # Save to temporary file
        temp_cert_file = os.path.join(integration_ctx.temp_dir, "received_ca.pem")
        with open(temp_cert_file, "w") as f:
            f.write(received_cert)

        # Verify file exists and content matches
        assert os.path.exists(temp_cert_file)

        with open(temp_cert_file, "r") as f:
            saved_cert = f.read()

        original_cert = cert_data.decode("utf-8")
        assert saved_cert == original_cert

    finally:
        await sharing.stop_sharing()


@pytest.mark.anyio
async def test_scenario_server_restart_after_failed_share(integration_ctx):
    """Test restarting sharing after a failed attempt"""
    ca_cert_path = integration_ctx.cert_manager.get_ca_cert_path()
    with open(ca_cert_path, "rb") as f:
        cert_data = f.read()

    sharing = CertificateSharing(
        cert_data=cert_data,
        host=integration_ctx.test_host,
        port=integration_ctx.test_port,
        timeout=5,
    )

    # First attempt - client uses wrong OTP
    success1, otp1 = await sharing.start_sharing()
    assert success1

    await asyncio.sleep(0.5)

    receiver = CertificateReceiver(
        server_host=integration_ctx.test_host,
        server_port=integration_ctx.test_port,
        timeout=3,
    )

    # Use wrong OTP
    fail_success, _, _ = await receiver.receive_certificate("000000")
    assert not fail_success

    # Stop first attempt
    await sharing.stop_sharing()

    # Wait a bit
    await asyncio.sleep(1)

    # Second attempt - use correct OTP
    success2, otp2 = await sharing.start_sharing()
    assert success2

    try:
        await asyncio.sleep(0.5)

        success, received_cert, _ = await receiver.receive_certificate(otp2)
        assert success
        assert received_cert is not None

    finally:
        await sharing.stop_sharing()


# ---------------------------------------------------------------------------
# Pairing callback failure (was TestPairingCallbackFailure)
# ---------------------------------------------------------------------------
@pytest.fixture
def callback_ctx(tmp_path):
    """A raising pairing_request_callback must surface
    CALLBACK_FAILED to the peer and invalidate the freshly-generated OTP."""
    cert_manager = CertificateManager(tmp_path)
    cert_manager.generate_ca()
    cert_manager.generate_server_certificate(
        ip_addresses=["127.0.0.1"], hostname="test.local"
    )
    ca_cert_path = cert_manager.get_ca_cert_path()
    with open(ca_cert_path, "rb") as f:
        cert_data = f.read()

    return SimpleNamespace(
        cert_manager=cert_manager,
        cert_data=cert_data,
        test_host="127.0.0.1",
        test_port=15558,
        test_timeout=5,
    )


@pytest.mark.anyio
async def test_callback_exception_returns_callback_failed(callback_ctx):
    async def boom(_info):
        raise RuntimeError("GUI not reachable")

    sharing = CertificateSharing(
        cert_data=callback_ctx.cert_data,
        host=callback_ctx.test_host,
        port=callback_ctx.test_port,
        timeout=callback_ctx.test_timeout,
        pairing_request_callback=boom,
    )

    ok = await sharing.start_service()
    assert ok

    try:
        await asyncio.sleep(0.2)
        receiver = CertificateReceiver(
            server_host=callback_ctx.test_host,
            server_port=callback_ctx.test_port,
            timeout=3,
        )
        success, remaining, code = await receiver.request_pairing(hostname="testhost")
        assert not success
        assert remaining == 0
        assert code == "CALLBACK_FAILED"

        # OTP must be invalidated so it can't be brute-forced in the
        # 6-digit window before expiry.
        assert sharing.get_otp() is None
    finally:
        await sharing.stop_sharing()


# ---------------------------------------------------------------------------
# OTP never logged (was TestOtpNeverLogged)
# ---------------------------------------------------------------------------
@pytest.fixture
def otp_log_ctx(tmp_path):
    """The OTP value must never appear in logs at any level."""
    cert_manager = CertificateManager(tmp_path)
    cert_manager.generate_ca()
    ca_cert_path = cert_manager.get_ca_cert_path()
    with open(ca_cert_path, "rb") as f:
        cert_data = f.read()

    return SimpleNamespace(cert_manager=cert_manager, cert_data=cert_data)


@pytest.mark.anyio
async def test_start_sharing_does_not_log_otp_value(otp_log_ctx):
    sharing = CertificateSharing(
        cert_data=otp_log_ctx.cert_data,
        host="127.0.0.1",
        port=15559,
        timeout=4,
    )
    collected = []

    class _CaptureLogger:
        def log(self, msg, level, **kw):
            collected.append(str(msg))

        def info(self, msg, **kw):
            collected.append(str(msg))

        def warning(self, msg, **kw):
            collected.append(str(msg))

        def error(self, msg, **kw):
            collected.append(str(msg))

        def debug(self, msg, **kw):
            collected.append(str(msg))

        def exception(self, msg, **kw):
            collected.append(str(msg))

    sharing._logger = _CaptureLogger()

    ok, otp = await sharing.start_sharing()
    try:
        assert ok
        assert otp is not None
        for line in collected:
            assert otp not in line, f"OTP value leaked in log line: {line!r}"
        # Regression guard: the length info should still surface so
        # we don't silently lose all OTP audit signals.
        assert any("len=" in line for line in collected), (
            f"expected len= in logs, got: {collected}"
        )
    finally:
        await sharing.stop_sharing()


# ---------------------------------------------------------------------------
# CertificateManager atomic writes (was TestCertificateManagerAtomicWrites)
# ---------------------------------------------------------------------------
@pytest.fixture
def cert_manager(tmp_path):
    """A bare CertificateManager rooted at an isolated temp directory."""
    return CertificateManager(tmp_path)


@pytest.mark.skipif(os.name == "nt", reason="POSIX-only chmod semantics")
def test_generate_ca_sets_key_mode_0o600(cert_manager):
    import stat

    assert cert_manager.generate_ca()
    mode = stat.S_IMODE(os.stat(cert_manager.ca_key_path).st_mode)
    assert mode == 0o600, f"CA private key has perms {oct(mode)}, expected 0o600"


@pytest.mark.skipif(os.name == "nt", reason="POSIX-only chmod semantics")
def test_generate_ca_sets_cert_mode_0o644(cert_manager):
    import stat

    assert cert_manager.generate_ca()
    mode = stat.S_IMODE(os.stat(cert_manager.ca_cert_path).st_mode)
    assert mode == 0o644, f"CA cert has perms {oct(mode)}, expected 0o644"


@pytest.mark.skipif(os.name == "nt", reason="POSIX-only chmod semantics")
def test_generate_server_cert_sets_key_mode_0o600(cert_manager):
    import stat

    assert cert_manager.generate_ca()
    assert cert_manager.generate_server_certificate(
        hostname="test.local", ip_addresses=["127.0.0.1"]
    )
    mode = stat.S_IMODE(os.stat(cert_manager.server_key_path).st_mode)
    assert mode == 0o600, f"server private key has perms {oct(mode)}, expected 0o600"


def test_atomic_write_preserves_old_cert_on_crash(cert_manager):
    """A failing rename leaves the previous CA on disk intact (atomicity)."""
    import unittest.mock as mock

    # First successful generation as a baseline.
    assert cert_manager.generate_ca()
    original_key = cert_manager.ca_key_path.read_bytes()

    # Force a regeneration; mock os.replace to fail mid-flight on the key.
    original_replace = os.replace
    ca_key_str = str(cert_manager.ca_key_path)

    def failing_replace(src, dst):
        if str(dst) == ca_key_str:
            raise OSError("simulated rename failure")
        return original_replace(src, dst)

    with mock.patch("utils.fs.os.replace", side_effect=failing_replace):
        ok = cert_manager.generate_ca(force=True)
        assert not ok, "generate_ca should report failure"

    # Old key is intact, no torn temp left behind.
    assert cert_manager.ca_key_path.read_bytes() == original_key
    leftovers = [p for p in cert_manager.cert_dir.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == [], f"temp files leaked: {leftovers}"


# ---------------------------------------------------------------------------
# OTP hardening and CSR signing (was TestOtpHardeningAndCsrSigning)
# ---------------------------------------------------------------------------
@pytest.fixture
def hardening_ctx(tmp_path):
    """OTP is verified server-side and burns after too many wrong attempts;
    a CSR sent with a valid OTP is signed and the client cert returned."""
    cert_manager = CertificateManager(tmp_path)
    cert_manager.generate_ca()
    cert_manager.generate_server_certificate(
        ip_addresses=["127.0.0.1"], hostname="test.local"
    )
    with open(cert_manager.ca_cert_path, "rb") as f:
        cert_data = f.read()

    return SimpleNamespace(
        cert_manager=cert_manager,
        cert_data=cert_data,
        test_host="127.0.0.1",
        test_port=15570,
    )


@pytest.mark.anyio
async def test_wrong_otp_rejected_then_locked_out(hardening_ctx):
    from utils.crypto.sharing import MAX_OTP_ATTEMPTS

    sharing = CertificateSharing(
        cert_data=hardening_ctx.cert_data,
        host=hardening_ctx.test_host,
        port=hardening_ctx.test_port,
        timeout=30,
    )
    assert await sharing.start_service()
    try:
        otp, _ = await sharing.ensure_active_otp()
        assert otp is not None

        # Burn through the allowed wrong attempts.
        for _ in range(MAX_OTP_ATTEMPTS):
            receiver = CertificateReceiver(
                server_host=hardening_ctx.test_host,
                server_port=hardening_ctx.test_port,
                timeout=3,
            )
            ok, ca, _client = await receiver.receive_certificate("000000")
            assert not ok

        # OTP is now burned: even the correct OTP no longer works.
        receiver = CertificateReceiver(
            server_host=hardening_ctx.test_host,
            server_port=hardening_ctx.test_port,
            timeout=3,
        )
        ok, ca, _client = await receiver.receive_certificate(otp)
        assert not ok
    finally:
        await sharing.stop_sharing()


@pytest.mark.anyio
async def test_valid_otp_with_csr_returns_signed_client_cert(hardening_ctx):
    import shutil as _shutil

    client_dir = tempfile.mkdtemp()
    client_cm = CertificateManager(Path(client_dir))
    # The server assigns the UID: the signer ignores the CSR CN and
    # stamps a fixed uid here (a real Server mints a unique one).
    assigned_uid = "server-assigned-e2e"

    def _assign(csr_pem):
        return hardening_ctx.cert_manager.sign_client_csr(csr_pem, assigned_uid)

    sharing = CertificateSharing(
        cert_data=hardening_ctx.cert_data,
        host=hardening_ctx.test_host,
        port=hardening_ctx.test_port + 1,
        timeout=30,
        csr_signer=_assign,
    )
    ok, otp = await sharing.start_sharing()
    try:
        assert ok
        csr_pem = client_cm.generate_client_key_and_csr()
        receiver = CertificateReceiver(
            server_host=hardening_ctx.test_host,
            server_port=hardening_ctx.test_port + 1,
            timeout=3,
        )
        success, ca_cert, client_cert = await receiver.receive_certificate(
            otp, csr_pem=csr_pem
        )
        assert success
        assert ca_cert is not None
        assert client_cert is not None
        assert "BEGIN CERTIFICATE" in client_cert
        # The issued cert carries the server-assigned UID as its CN.
        assert (
            CertificateManager.read_certificate_common_name(client_cert) == assigned_uid
        )
    finally:
        await sharing.stop_sharing()
        _shutil.rmtree(client_dir, ignore_errors=True)
