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

import unittest
import asyncio
import tempfile
import os
import shutil
from pathlib import Path
import time

from utils.crypto.sharing import CertificateSharing, CertificateReceiver
from utils.crypto import CertificateManager


class TestCertificateSharing(unittest.TestCase):
    """Test suite for CertificateSharing class"""

    def setUp(self):
        """Set up test fixtures"""
        # Create temporary directory for test certificates
        self.temp_dir = tempfile.mkdtemp()
        self.cert_manager = CertificateManager(Path(self.temp_dir))

        # Generate test certificates
        self.cert_manager.generate_ca()
        self.cert_manager.generate_server_certificate(
            ip_addresses=["127.0.0.1"], hostname="test.local"
        )

        # Load CA certificate data
        ca_cert_path = self.cert_manager.get_ca_cert_path()
        if ca_cert_path is None:
            self.fail("CA certificate path not found")

        with open(file=ca_cert_path, mode="rb") as f:  # ty:ignore[no-matching-overload]
            self.cert_data = f.read()

        # Test configuration
        self.test_host = "127.0.0.1"
        self.test_port = 15556  # Use non-standard port for testing
        self.test_timeout = 5

    def tearDown(self):
        """Clean up test fixtures"""
        # Remove temporary directory
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_otp_generation(self):
        """Test OTP generation is secure and correct length"""
        otp = CertificateSharing._generate_otp()

        # Check OTP is 6 digits
        self.assertEqual(len(otp), 6)

        # Check OTP contains only digits
        self.assertTrue(otp.isdigit())

        # Check multiple OTPs are different (statistical test)
        otps = [CertificateSharing._generate_otp() for _ in range(100)]
        unique_otps = set(otps)
        self.assertGreater(len(unique_otps), 90)  # At least 90% unique

    def test_envelope_creation_and_decryption(self):
        """The certificate envelope is base64(JSON) of AES-GCM fields (no JWT)."""
        import base64
        import json

        sharing = CertificateSharing(
            cert_data=self.cert_data,
            host=self.test_host,
            port=self.test_port,
            timeout=self.test_timeout,
        )

        otp = "123456"
        token = asyncio.run(sharing._create_envelope(otp))

        # Token should be a non-empty string
        self.assertIsInstance(token, str)
        self.assertGreater(len(token), 0)

        # It decodes as base64(JSON) - NOT a JWT (no dotted header.payload.sig).
        payload = json.loads(base64.b64decode(token))

        # Check payload carries the AES-GCM fields; no signature / exp / iat.
        self.assertIn("encrypted_cert", payload)
        self.assertIn("nonce", payload)
        self.assertIn("salt", payload)
        self.assertNotIn("exp", payload)
        self.assertNotIn("iat", payload)

        # Check salt is base64 encoded string
        self.assertIsInstance(payload["salt"], str)
        self.assertGreater(len(payload["salt"]), 0)

        # Decrypt certificate data using salt from payload
        decrypted_cert = CertificateSharing.decrypt_data(
            encrypted_data=base64.b64decode(payload["encrypted_cert"]),
            nonce=base64.b64decode(payload["nonce"]),
            salt=base64.b64decode(payload["salt"]),
            otp=otp,
        )

        # Check decrypted certificate matches original
        cert_str = (
            self.cert_data.decode("utf-8")
            if isinstance(self.cert_data, bytes)
            else self.cert_data
        )
        # Remove any leading/trailing whitespace for comparison
        cert_str_normalized = cert_str.replace("\r\n", "\n").replace("\r", "\n")
        decrypted_cert_normalized = (
            decrypted_cert.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
        )
        self.assertEqual(decrypted_cert_normalized, cert_str_normalized)

    def test_otp_expiry(self):
        """Test OTP expiry mechanism"""
        sharing = CertificateSharing(
            cert_data=self.cert_data,
            host=self.test_host,
            port=self.test_port,
            timeout=1,  # 1 second timeout
        )

        # Initially no OTP
        self.assertFalse(sharing._is_otp_valid())

        # Generate OTP
        sharing._otp = "123456"
        sharing._otp_expiry = time.time() + 1

        # Should be valid
        self.assertTrue(sharing._is_otp_valid())

        # Wait for expiry
        time.sleep(1.1)

        # Should be expired
        self.assertFalse(sharing._is_otp_valid())

    def test_start_sharing_success(self):
        """Test successful sharing server start"""

        async def run_test():
            sharing = CertificateSharing(
                cert_data=self.cert_data,
                host=self.test_host,
                port=self.test_port,
                timeout=self.test_timeout,
            )

            success, otp = await sharing.start_sharing()

            try:
                # Check success
                self.assertTrue(success)
                self.assertIsNotNone(otp)
                self.assertEqual(len(otp), 6)  # ty:ignore[invalid-argument-type]
                self.assertTrue(otp.isdigit())  # ty:ignore[possibly-missing-attribute]

                # Check server is running
                self.assertTrue(sharing.is_sharing_active())

                # Check OTP can be retrieved
                retrieved_otp = sharing.get_otp()
                self.assertEqual(retrieved_otp, otp)

            finally:
                await sharing.stop_sharing()

        asyncio.run(run_test())

    def test_start_sharing_twice(self):
        """Test starting sharing when already active"""

        async def run_test():
            sharing = CertificateSharing(
                cert_data=self.cert_data,
                host=self.test_host,
                port=self.test_port,
                timeout=self.test_timeout,
            )

            # Start first time
            success1, otp1 = await sharing.start_sharing()
            self.assertTrue(success1)

            # Try to start second time
            success2, otp2 = await sharing.start_sharing()
            self.assertFalse(success2)
            self.assertIsNone(otp2)

            await sharing.stop_sharing()

        asyncio.run(run_test())

    def test_stop_sharing(self):
        """Test stopping sharing server"""

        async def run_test():
            sharing = CertificateSharing(
                cert_data=self.cert_data,
                host=self.test_host,
                port=self.test_port,
                timeout=self.test_timeout,
            )

            # Start sharing
            await sharing.start_sharing()
            self.assertTrue(sharing.is_sharing_active())

            # Stop sharing
            await sharing.stop_sharing()
            self.assertFalse(sharing.is_sharing_active())

            # OTP should be invalidated
            self.assertIsNone(sharing.get_otp())

        asyncio.run(run_test())

    def test_auto_shutdown(self):
        """Test automatic shutdown after timeout"""

        async def run_test():
            sharing = CertificateSharing(
                cert_data=self.cert_data,
                host=self.test_host,
                port=self.test_port,
                timeout=2,  # 2 seconds
            )

            await sharing.start_sharing()
            self.assertTrue(sharing.is_sharing_active())

            # Wait for auto shutdown
            await asyncio.sleep(2.5)

            # Should be stopped
            self.assertFalse(sharing.is_sharing_active())

        asyncio.run(run_test())

    def test_full_sharing_workflow(self):
        """Test complete sharing workflow from server to client"""

        async def run_test():
            # Start sharing server
            sharing = CertificateSharing(
                cert_data=self.cert_data,
                host=self.test_host,
                port=self.test_port,
                timeout=10,
            )

            success, otp = await sharing.start_sharing()
            self.assertTrue(success)
            self.assertIsNotNone(otp)

            try:
                # Give server time to start
                await asyncio.sleep(0.5)

                # Connect as client
                receiver = CertificateReceiver(
                    server_host=self.test_host, server_port=self.test_port, timeout=5
                )

                success, received_cert, _ = await receiver.receive_certificate(otp)  # ty:ignore[invalid-argument-type]

                # Check success
                self.assertTrue(success)
                self.assertIsNotNone(received_cert)

                # Check certificate matches original
                original_cert = self.cert_data.decode("utf-8")
                self.assertEqual(received_cert, original_cert)

                # Check sharing was marked as successful
                self.assertTrue(sharing.was_shared())

            finally:
                await sharing.stop_sharing()

        asyncio.run(run_test())

    def test_receive_with_wrong_otp(self):
        """Test certificate reception with wrong OTP"""

        async def run_test():
            # Start sharing server
            sharing = CertificateSharing(
                cert_data=self.cert_data,
                host=self.test_host,
                port=self.test_port,
                timeout=10,
            )

            success, correct_otp = await sharing.start_sharing()
            self.assertTrue(success)

            try:
                await asyncio.sleep(0.5)

                # Try to receive with wrong OTP
                receiver = CertificateReceiver(
                    server_host=self.test_host, server_port=self.test_port, timeout=5
                )

                wrong_otp = "000000"  # Wrong OTP
                success, received_cert, _ = await receiver.receive_certificate(
                    wrong_otp
                )

                # Should fail
                self.assertFalse(success)
                self.assertIsNone(received_cert)

            finally:
                await sharing.stop_sharing()

        asyncio.run(run_test())

    def test_receive_after_expiry(self):
        """Test certificate reception after OTP expiry"""

        async def run_test():
            # Start sharing server with short timeout
            sharing = CertificateSharing(
                cert_data=self.cert_data,
                host=self.test_host,
                port=self.test_port,
                timeout=1,  # 1 second
            )

            success, otp = await sharing.start_sharing()
            self.assertTrue(success)
            self.assertIsNotNone(otp)

            try:
                # Wait for OTP to expire
                await asyncio.sleep(1.5)

                # Try to receive after expiry
                receiver = CertificateReceiver(
                    server_host=self.test_host, server_port=self.test_port, timeout=5
                )

                success, received_cert, _ = await receiver.receive_certificate(otp)

                # Should fail (server auto-shutdown)
                self.assertFalse(success)
                self.assertIsNone(received_cert)

            finally:
                # Try to stop (should already be stopped)
                await sharing.stop_sharing()

        asyncio.run(run_test())

    def test_receive_connection_refused(self):
        """Test certificate reception when server is not running"""

        async def run_test():
            receiver = CertificateReceiver(
                server_host=self.test_host, server_port=self.test_port, timeout=2
            )

            # Try to receive without server running
            success, received_cert, _ = await receiver.receive_certificate("123456")

            # Should fail
            self.assertFalse(success)
            self.assertIsNone(received_cert)

        asyncio.run(run_test())

    def test_otp_is_single_use(self):
        """An OTP is consumed on first successful delivery; a replay fails.

        This is the anti-replay guarantee: a passive eavesdropper can only
        capture the token after the legitimate client received it, by which
        point the OTP is already burned.
        """

        async def run_test():
            sharing = CertificateSharing(
                cert_data=self.cert_data,
                host=self.test_host,
                port=self.test_port,
                timeout=10,
            )

            success, otp = await sharing.start_sharing()
            self.assertTrue(success)

            try:
                await asyncio.sleep(0.3)

                # First client succeeds and consumes the OTP.
                first = CertificateReceiver(
                    server_host=self.test_host,
                    server_port=self.test_port,
                    timeout=5,
                )
                ok1, cert1, _ = await first.receive_certificate(otp)
                self.assertTrue(ok1)
                self.assertIsNotNone(cert1)

                # A replay with the same (now consumed) OTP is rejected.
                replay = CertificateReceiver(
                    server_host=self.test_host,
                    server_port=self.test_port,
                    timeout=5,
                )
                ok2, cert2, _ = await replay.receive_certificate(otp)
                self.assertFalse(ok2)
                self.assertIsNone(cert2)

            finally:
                await sharing.stop_sharing()

        asyncio.run(run_test())

    def test_receiver_timeout(self):
        """Test receiver timeout when server doesn't respond"""

        async def run_test():
            # Start server but don't send anything
            async def dummy_handler(reader, writer):
                # Just wait and do nothing
                await asyncio.sleep(10)
                writer.close()

            server = await asyncio.start_server(
                dummy_handler, self.test_host, self.test_port
            )

            try:
                receiver = CertificateReceiver(
                    server_host=self.test_host,
                    server_port=self.test_port,
                    timeout=1,  # Short timeout
                )

                success, cert, _ = await receiver.receive_certificate("123456")

                # Should timeout and fail
                self.assertFalse(success)
                self.assertIsNone(cert)

            finally:
                server.close()
                await server.wait_closed()

        asyncio.run(run_test())


class TestCertificateSharingIntegration(unittest.TestCase):
    """Integration tests for complete certificate sharing workflow"""

    def setUp(self):
        """Set up test fixtures"""
        self.temp_dir = tempfile.mkdtemp()
        self.cert_manager = CertificateManager(Path(self.temp_dir))
        self.cert_manager.generate_ca()
        self.cert_manager.generate_server_certificate(
            ip_addresses=["127.0.0.1"], hostname="test.local"
        )

        self.test_host = "127.0.0.1"
        self.test_port = 15557
        self.test_timeout = 10

    def tearDown(self):
        """Clean up test fixtures"""
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_complete_workflow_with_file_save(self):
        """Test complete workflow including saving received certificate"""

        async def run_test():
            # Load certificate
            ca_cert_path = self.cert_manager.get_ca_cert_path()
            with open(ca_cert_path, "rb") as f:
                cert_data = f.read()

            # Start sharing
            sharing = CertificateSharing(
                cert_data=cert_data,
                host=self.test_host,
                port=self.test_port,
                timeout=self.test_timeout,
            )

            success, otp = await sharing.start_sharing()
            self.assertTrue(success)

            try:
                await asyncio.sleep(0.5)

                # Receive certificate
                receiver = CertificateReceiver(
                    server_host=self.test_host, server_port=self.test_port, timeout=5
                )

                success, received_cert, _ = await receiver.receive_certificate(otp)
                self.assertTrue(success)

                # Save to temporary file
                temp_cert_file = os.path.join(self.temp_dir, "received_ca.pem")
                with open(temp_cert_file, "w") as f:
                    f.write(received_cert)

                # Verify file exists and content matches
                self.assertTrue(os.path.exists(temp_cert_file))

                with open(temp_cert_file, "r") as f:
                    saved_cert = f.read()

                original_cert = cert_data.decode("utf-8")
                self.assertEqual(saved_cert, original_cert)

            finally:
                await sharing.stop_sharing()

        asyncio.run(run_test())

    def test_scenario_server_restart_after_failed_share(self):
        """Test restarting sharing after a failed attempt"""

        async def run_test():
            ca_cert_path = self.cert_manager.get_ca_cert_path()
            with open(ca_cert_path, "rb") as f:
                cert_data = f.read()

            sharing = CertificateSharing(
                cert_data=cert_data, host=self.test_host, port=self.test_port, timeout=5
            )

            # First attempt - client uses wrong OTP
            success1, otp1 = await sharing.start_sharing()
            self.assertTrue(success1)

            await asyncio.sleep(0.5)

            receiver = CertificateReceiver(
                server_host=self.test_host, server_port=self.test_port, timeout=3
            )

            # Use wrong OTP
            fail_success, _, _ = await receiver.receive_certificate("000000")
            self.assertFalse(fail_success)

            # Stop first attempt
            await sharing.stop_sharing()

            # Wait a bit
            await asyncio.sleep(1)

            # Second attempt - use correct OTP
            success2, otp2 = await sharing.start_sharing()
            self.assertTrue(success2)

            try:
                await asyncio.sleep(0.5)

                success, received_cert, _ = await receiver.receive_certificate(otp2)
                self.assertTrue(success)
                self.assertIsNotNone(received_cert)

            finally:
                await sharing.stop_sharing()

        asyncio.run(run_test())


class TestPairingCallbackFailure(unittest.TestCase):
    """A raising pairing_request_callback must surface
    CALLBACK_FAILED to the peer and invalidate the freshly-generated OTP."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.cert_manager = CertificateManager(Path(self.temp_dir))
        self.cert_manager.generate_ca()
        self.cert_manager.generate_server_certificate(
            ip_addresses=["127.0.0.1"], hostname="test.local"
        )
        ca_cert_path = self.cert_manager.get_ca_cert_path()
        with open(ca_cert_path, "rb") as f:
            self.cert_data = f.read()

        self.test_host = "127.0.0.1"
        self.test_port = 15558
        self.test_timeout = 5

    def tearDown(self):
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_callback_exception_returns_callback_failed(self):
        async def run_test():
            async def boom(_info):
                raise RuntimeError("GUI not reachable")

            sharing = CertificateSharing(
                cert_data=self.cert_data,
                host=self.test_host,
                port=self.test_port,
                timeout=self.test_timeout,
                pairing_request_callback=boom,
            )

            ok = await sharing.start_service()
            self.assertTrue(ok)

            try:
                await asyncio.sleep(0.2)
                receiver = CertificateReceiver(
                    server_host=self.test_host,
                    server_port=self.test_port,
                    timeout=3,
                )
                success, remaining, code = await receiver.request_pairing(
                    hostname="testhost"
                )
                self.assertFalse(success)
                self.assertEqual(remaining, 0)
                self.assertEqual(code, "CALLBACK_FAILED")

                # OTP must be invalidated so it can't be brute-forced in the
                # 6-digit window before expiry.
                self.assertIsNone(sharing.get_otp())
            finally:
                await sharing.stop_sharing()

        asyncio.run(run_test())


class TestOtpNeverLogged(unittest.TestCase):
    """The OTP value must never appear in logs at any level."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.cert_manager = CertificateManager(Path(self.temp_dir))
        self.cert_manager.generate_ca()
        ca_cert_path = self.cert_manager.get_ca_cert_path()
        with open(ca_cert_path, "rb") as f:
            self.cert_data = f.read()

    def tearDown(self):
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_start_sharing_does_not_log_otp_value(self):
        async def run_test():
            sharing = CertificateSharing(
                cert_data=self.cert_data,
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
                self.assertTrue(ok)
                self.assertIsNotNone(otp)
                for line in collected:
                    self.assertNotIn(
                        otp,
                        line,
                        f"OTP value leaked in log line: {line!r}",
                    )
                # Regression guard: the length info should still surface so
                # we don't silently lose all OTP audit signals.
                self.assertTrue(
                    any("len=" in line for line in collected),
                    f"expected len= in logs, got: {collected}",
                )
            finally:
                await sharing.stop_sharing()

        asyncio.run(run_test())


class TestCertificateManagerAtomicWrites(unittest.TestCase):
    """Verify CA/server key/cert files are written atomically with
    appropriate POSIX modes (0o600 for private keys, 0o644 for public certs)."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.cert_manager = CertificateManager(Path(self.temp_dir))

    def tearDown(self):
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @unittest.skipIf(os.name == "nt", "POSIX-only chmod semantics")
    def test_generate_ca_sets_key_mode_0o600(self):
        import stat

        self.assertTrue(self.cert_manager.generate_ca())
        mode = stat.S_IMODE(os.stat(self.cert_manager.ca_key_path).st_mode)
        self.assertEqual(
            mode, 0o600, f"CA private key has perms {oct(mode)}, expected 0o600"
        )

    @unittest.skipIf(os.name == "nt", "POSIX-only chmod semantics")
    def test_generate_ca_sets_cert_mode_0o644(self):
        import stat

        self.assertTrue(self.cert_manager.generate_ca())
        mode = stat.S_IMODE(os.stat(self.cert_manager.ca_cert_path).st_mode)
        self.assertEqual(mode, 0o644, f"CA cert has perms {oct(mode)}, expected 0o644")

    @unittest.skipIf(os.name == "nt", "POSIX-only chmod semantics")
    def test_generate_server_cert_sets_key_mode_0o600(self):
        import stat

        self.assertTrue(self.cert_manager.generate_ca())
        self.assertTrue(
            self.cert_manager.generate_server_certificate(
                hostname="test.local", ip_addresses=["127.0.0.1"]
            )
        )
        mode = stat.S_IMODE(os.stat(self.cert_manager.server_key_path).st_mode)
        self.assertEqual(
            mode, 0o600, f"server private key has perms {oct(mode)}, expected 0o600"
        )

    def test_atomic_write_preserves_old_cert_on_crash(self):
        """A failing rename leaves the previous CA on disk intact (atomicity)."""
        import unittest.mock as mock

        # First successful generation as a baseline.
        self.assertTrue(self.cert_manager.generate_ca())
        original_key = self.cert_manager.ca_key_path.read_bytes()

        # Force a regeneration; mock os.replace to fail mid-flight on the key.
        original_replace = os.replace
        ca_key_str = str(self.cert_manager.ca_key_path)

        def failing_replace(src, dst):
            if str(dst) == ca_key_str:
                raise OSError("simulated rename failure")
            return original_replace(src, dst)

        with mock.patch("utils.fs.os.replace", side_effect=failing_replace):
            ok = self.cert_manager.generate_ca(force=True)
            self.assertFalse(ok, "generate_ca should report failure")

        # Old key is intact, no torn temp left behind.
        self.assertEqual(self.cert_manager.ca_key_path.read_bytes(), original_key)
        leftovers = [
            p for p in self.cert_manager.cert_dir.iterdir() if p.name.endswith(".tmp")
        ]
        self.assertEqual(leftovers, [], f"temp files leaked: {leftovers}")


class TestOtpHardeningAndCsrSigning(unittest.TestCase):
    """OTP is verified server-side and burns after too many wrong attempts;
    a CSR sent with a valid OTP is signed and the client cert returned."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.cert_manager = CertificateManager(Path(self.temp_dir))
        self.cert_manager.generate_ca()
        self.cert_manager.generate_server_certificate(
            ip_addresses=["127.0.0.1"], hostname="test.local"
        )
        with open(self.cert_manager.ca_cert_path, "rb") as f:
            self.cert_data = f.read()
        self.test_host = "127.0.0.1"
        self.test_port = 15570

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_wrong_otp_rejected_then_locked_out(self):
        from utils.crypto.sharing import MAX_OTP_ATTEMPTS

        async def run_test():
            sharing = CertificateSharing(
                cert_data=self.cert_data,
                host=self.test_host,
                port=self.test_port,
                timeout=30,
            )
            self.assertTrue(await sharing.start_service())
            try:
                otp, _ = await sharing.ensure_active_otp()
                self.assertIsNotNone(otp)

                # Burn through the allowed wrong attempts.
                for _ in range(MAX_OTP_ATTEMPTS):
                    receiver = CertificateReceiver(
                        server_host=self.test_host,
                        server_port=self.test_port,
                        timeout=3,
                    )
                    ok, ca, _client = await receiver.receive_certificate("000000")
                    self.assertFalse(ok)

                # OTP is now burned: even the correct OTP no longer works.
                receiver = CertificateReceiver(
                    server_host=self.test_host,
                    server_port=self.test_port,
                    timeout=3,
                )
                ok, ca, _client = await receiver.receive_certificate(otp)
                self.assertFalse(ok)
            finally:
                await sharing.stop_sharing()

        asyncio.run(run_test())

    def test_valid_otp_with_csr_returns_signed_client_cert(self):
        import shutil as _shutil

        async def run_test():
            client_dir = tempfile.mkdtemp()
            client_cm = CertificateManager(Path(client_dir))
            sharing = CertificateSharing(
                cert_data=self.cert_data,
                host=self.test_host,
                port=self.test_port + 1,
                timeout=30,
                csr_signer=self.cert_manager.sign_client_csr,
            )
            ok, otp = await sharing.start_sharing()
            try:
                self.assertTrue(ok)
                csr_pem = client_cm.generate_client_key_and_csr("client-uid-e2e")
                receiver = CertificateReceiver(
                    server_host=self.test_host,
                    server_port=self.test_port + 1,
                    timeout=3,
                )
                success, ca_cert, client_cert = await receiver.receive_certificate(
                    otp, csr_pem=csr_pem
                )
                self.assertTrue(success)
                self.assertIsNotNone(ca_cert)
                self.assertIsNotNone(client_cert)
                self.assertIn("BEGIN CERTIFICATE", client_cert)
            finally:
                await sharing.stop_sharing()
                _shutil.rmtree(client_dir, ignore_errors=True)

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
