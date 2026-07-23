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

"""Tests for the clean-up performed when the client switches server.

When a client picks a *different* server than the one it already trusts,
``Client._forget_previous_server`` must drop the old server's trust material:
its pinned CA (``ca_<id>.crt`` + every mapping alias) and the machine's client
identity (signed by the old CA, worthless against the new one). This forces a
fresh OTP re-pairing with the new server instead of retrying stale credentials.
"""

import tempfile
import shutil
import unittest
from pathlib import Path

from utils.crypto import CertificateManager
from service.client import Client


class TestForgetPreviousServer(unittest.TestCase):
    def setUp(self):
        self.server_dir = tempfile.mkdtemp()
        self.client_dir = tempfile.mkdtemp()

        # Server A: mint a CA and issue a client certificate.
        self.server_cm = CertificateManager(Path(self.server_dir))
        self.server_cm.generate_ca()
        self.server_cm.generate_server_certificate(
            ip_addresses=["127.0.0.1"], hostname="server-a.local"
        )

        # Build a Client without touching disk config, then point its cert
        # manager at an isolated temp directory.
        self.client = Client(auto_load_config=False)
        self.client._cert_manager = CertificateManager(Path(self.client_dir))
        self.cm = self.client._cert_manager

    def tearDown(self):
        shutil.rmtree(self.server_dir, ignore_errors=True)
        shutil.rmtree(self.client_dir, ignore_errors=True)

    def _pair_with_server_a(self):
        """Seed the client with server A's CA and a client identity."""
        csr_pem = self.cm.generate_client_key_and_csr()
        cert_pem = self.server_cm.sign_client_csr(csr_pem, "client-uid")
        self.assertTrue(self.cm.save_client_certificate(cert_pem))
        with open(self.server_cm.ca_cert_path, "rb") as f:
            self.assertTrue(self.cm.save_ca_data(f.read(), "A"))

    def test_forget_previous_server_wipes_ca_and_client_identity(self):
        self._pair_with_server_a()
        # Precondition: both trust artefacts are present.
        self.assertIsNotNone(self.cm.get_ca_cert_path("A"))
        self.assertTrue(self.cm.client_credentials_exist())

        self.client._forget_previous_server("A", "127.0.0.1", "server-a.local")

        # The old CA and the stale client identity are both gone.
        self.assertIsNone(self.cm.get_ca_cert_path("A"))
        self.assertFalse(self.cm.client_credentials_exist())

    def test_forget_previous_server_is_best_effort_when_nothing_stored(self):
        # No CA and no client identity: cleanup must not raise.
        self.assertIsNone(self.cm.get_ca_cert_path("A"))
        self.assertFalse(self.cm.client_credentials_exist())

        self.client._forget_previous_server("A", "127.0.0.1", "server-a.local")

        self.assertIsNone(self.cm.get_ca_cert_path("A"))
        self.assertFalse(self.cm.client_credentials_exist())


if __name__ == "__main__":
    unittest.main()
