# tests/test_cert_sharing.py
"""
Unit tests for certificate sharing system with OTP and JWT
"""

import unittest
import asyncio
import tempfile
import os
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

    def test_jwt_creation_and_decryption(self):
        """Test JWT creation and decryption with OTP"""
        sharing = CertificateSharing(
            cert_data=self.cert_data,
            host=self.test_host,
            port=self.test_port,
            timeout=self.test_timeout,
        )

        otp = "123456"
        token = sharing._create_jwt(otp)

        # Token should be a non-empty string
        self.assertIsInstance(token, str)
        self.assertGreater(len(token), 0)

        # Should be able to decode with same OTP
        import jwt

        payload = jwt.decode(token, otp, algorithms=["HS256"])

        # Check payload contains certificate
        self.assertIn("cert", payload)
        self.assertIn("exp", payload)
        self.assertIn("iat", payload)

        # Check certificate data matches
        cert_str = (
            self.cert_data.decode("utf-8")
            if isinstance(self.cert_data, bytes)
            else self.cert_data
        )
        self.assertEqual(payload["cert"], cert_str)

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

                success, received_cert = await receiver.receive_certificate(otp)  # ty:ignore[invalid-argument-type]

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
                success, received_cert = await receiver.receive_certificate(wrong_otp)

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

            try:
                # Wait for OTP to expire
                await asyncio.sleep(1.5)

                # Try to receive after expiry
                receiver = CertificateReceiver(
                    server_host=self.test_host, server_port=self.test_port, timeout=5
                )

                success, received_cert = await receiver.receive_certificate(otp)

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
            success, received_cert = await receiver.receive_certificate("123456")

            # Should fail
            self.assertFalse(success)
            self.assertIsNone(received_cert)

        asyncio.run(run_test())

    def test_multiple_clients(self):
        """Test multiple clients receiving certificate"""

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
                await asyncio.sleep(0.5)

                # Create multiple receivers
                async def receive_cert(client_id):
                    receiver = CertificateReceiver(
                        server_host=self.test_host,
                        server_port=self.test_port,
                        timeout=5,
                    )
                    return await receiver.receive_certificate(otp)

                # Receive from multiple clients concurrently
                results = await asyncio.gather(
                    receive_cert(1), receive_cert(2), receive_cert(3)
                )

                # All should succeed
                for success, cert in results:
                    self.assertTrue(success)
                    self.assertIsNotNone(cert)

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

                success, cert = await receiver.receive_certificate("123456")

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

                success, received_cert = await receiver.receive_certificate(otp)
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
            fail_success, _ = await receiver.receive_certificate("000000")
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

                success, received_cert = await receiver.receive_certificate(otp2)
                self.assertTrue(success)
                self.assertIsNotNone(received_cert)

            finally:
                await sharing.stop_sharing()

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
