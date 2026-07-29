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

"""Certificate generation must survive any hostname the OS reports.

X.509 caps a Common Name at 64 characters while a DNS name may run to 253, so a
long-but-valid FQDN (routine on CI runners and corporate networks) used to make
``generate_server_certificate`` fail outright. The full name belongs in the SAN
- which is what TLS verification actually reads - and only the CN is clamped.
"""

from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.x509.oid import NameOID

from utils.crypto import (
    _COMMON_NAME_MAX_LEN,
    CertificateManager,
    _certificate_hostname,
)

# Valid labels throughout, 71 characters in total: passes DNS sanitization but
# exceeds the Common Name limit.
LONG_FQDN = "a" * 40 + "." + "b" * 30


def _common_name(cm: CertificateManager) -> str:
    with open(cm.server_cert_path, "rb") as f:
        cert = x509.load_pem_x509_certificate(f.read(), default_backend())
    return cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value


def test_certificate_hostname_keeps_usable_names():
    assert _certificate_hostname("host.local") == "host.local"
    assert _certificate_hostname("Mac-1751297693169.local") == "Mac-1751297693169.local"


def test_certificate_hostname_falls_back_on_unusable_names():
    # Spaces and underscores are not valid in DNS labels.
    assert _certificate_hostname("bad host_name") == "perpetua.local"
    assert _certificate_hostname("") == "perpetua.local"
    # A single label over 63 characters cannot be a DNS name at all.
    assert _certificate_hostname("a" * 70) == "perpetua.local"


def test_long_fqdn_generates_certificate_with_full_san(tmp_path):
    cm = CertificateManager(tmp_path / "server")
    assert cm.generate_ca(force=True)
    assert cm.generate_server_certificate(
        hostname=LONG_FQDN, ip_addresses=["192.168.1.10"], force=True
    )

    _, san_dns = cm.get_server_cert_san()
    assert LONG_FQDN in san_dns

    cn = _common_name(cm)
    assert len(cn) <= _COMMON_NAME_MAX_LEN
    # The first label is a meaningful identity, so it is preferred over the
    # generic fallback.
    assert cn == "a" * 40


def test_short_hostname_keeps_itself_as_common_name(tmp_path):
    cm = CertificateManager(tmp_path / "server")
    assert cm.generate_ca(force=True)
    assert cm.generate_server_certificate(
        hostname="host.local", ip_addresses=["192.168.1.10"], force=True
    )

    assert _common_name(cm) == "host.local"


def test_generate_ca_regenerates_when_key_is_missing(tmp_path):
    """A cert without its key is not a usable CA - it must be re-created."""
    cm = CertificateManager(tmp_path / "server")
    assert cm.generate_ca()
    cm.ca_key_path.unlink()

    assert cm.generate_ca()
    assert cm.ca_key_path.exists()
    # And the leaf can actually be signed with it.
    assert cm.generate_server_certificate(
        hostname="host.local", ip_addresses=["192.168.1.10"], force=True
    )
