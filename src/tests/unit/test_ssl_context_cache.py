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

"""SSL context caching vs certificates rewritten in place.

Both connection handlers cache their SSLContext, and the paths do not change
when a certificate is re-issued or re-paired over the top of the old one. A
path-only cache key therefore keeps serving dead key material until the
process restarts - which silently defeated the runtime SAN widening.
"""

import pytest

from network.connection.client import ConnectionHandler as ClientHandler
from network.connection.server import ConnectionHandler as ServerHandler
from utils.crypto import CertificateManager


@pytest.fixture
def certs(tmp_path):
    """Real CA + leaf, so the contexts actually load something."""
    cm = CertificateManager(tmp_path / "certs")
    assert cm.generate_ca(force=True)
    assert cm.generate_server_certificate(
        hostname="host.local", ip_addresses=["10.0.0.1"], force=True
    )
    certfile, keyfile = cm.get_server_credentials()
    return cm, certfile, keyfile


class TestServerContextCache:
    def test_unchanged_files_reuse_the_context(self, certs):
        """Caching must still work - parsing a chain per handshake is wasteful."""
        _, certfile, keyfile = certs
        handler = ServerHandler(certfile=certfile, keyfile=keyfile, ssl_enabled=True)

        assert handler._get_ssl_context() is handler._get_ssl_context()

    def test_invalidate_forces_a_rebuild(self, certs):
        """This method did not exist: refresh_advertisement looked it up with
        getattr and skipped silently, so a re-issued leaf never reached the
        wire until the server was restarted."""
        _, certfile, keyfile = certs
        handler = ServerHandler(certfile=certfile, keyfile=keyfile, ssl_enabled=True)
        first = handler._get_ssl_context()

        handler.invalidate_ssl_context()

        assert handler._get_ssl_context() is not first

    def test_no_certificates_means_no_context(self):
        handler = ServerHandler(ssl_enabled=True)

        assert handler._get_ssl_context() is None


class TestClientContextCache:
    def test_unchanged_files_reuse_the_context(self, certs):
        _, certfile, _ = certs
        handler = ClientHandler(certfile=certfile, use_ssl=True)

        assert handler._get_ssl_context() is handler._get_ssl_context()

    def test_rewriting_the_ca_in_place_invalidates_the_cache(self, certs, tmp_path):
        """Re-pairing writes a *new* CA to the *same* path.

        Keyed on paths alone the client would keep trusting the old CA and
        reject the new server for the lifetime of the process.
        """
        cm, certfile, _ = certs
        handler = ClientHandler(certfile=certfile, use_ssl=True)
        first = handler._get_ssl_context()

        other = CertificateManager(tmp_path / "other")
        assert other.generate_ca(force=True)
        with open(other.ca_cert_path, "rb") as src:
            replacement = src.read()
        with open(certfile, "wb") as dst:
            dst.write(replacement)

        assert handler._get_ssl_context() is not first

    def test_explicit_invalidation_also_works(self, certs):
        _, certfile, _ = certs
        handler = ClientHandler(certfile=certfile, use_ssl=True)
        first = handler._get_ssl_context()

        handler.invalidate_ssl_context()

        assert handler._get_ssl_context() is not first

    def test_missing_file_degrades_to_path_only(self, certs, tmp_path):
        """A stat failure must not raise out of the connect path."""
        _, certfile, _ = certs
        handler = ClientHandler(
            certfile=certfile,
            client_certfile=str(tmp_path / "absent.crt"),
            use_ssl=True,
        )

        assert handler._get_ssl_context() is not None
