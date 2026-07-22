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

"""Tests for the server handshake IP-admission decision.

These exercise the pure helpers that drive whether a connecting IP is accepted,
learned, or rejected - the logic behind the "same UID, different IP" fix
(a client reconnecting from a new DHCP address must not be denied).
"""

from model.client import ClientObj
from network.connection.server import ConnectionHandler


def _admission(client, address, uid, hostname):
    """Mirror the decision made inline in ``_handshake``.

    Returns one of "ok" (IP already known), "register" (learn the new IP),
    or "reject".
    """
    if ConnectionHandler._check_client(client, address):
        return "ok"
    identity_confirmed = ConnectionHandler._is_identity_confirmed(
        client, uid, hostname
    )
    if client.ip_addresses and not identity_confirmed:
        return "reject"
    return "register"


class TestIsIdentityConfirmed:
    def test_matching_uid_confirms(self):
        client = ClientObj(uid="abc", ip_addresses=["10.0.0.5"])
        assert ConnectionHandler._is_identity_confirmed(client, "abc", None) is True

    def test_matching_hostname_confirms(self):
        client = ClientObj(uid="abc", ip_addresses=["10.0.0.5"], hostname="host-a")
        assert (
            ConnectionHandler._is_identity_confirmed(client, None, "host-a") is True
        )

    def test_mismatched_uid_not_confirmed(self):
        client = ClientObj(uid="abc", ip_addresses=["10.0.0.5"])
        assert (
            ConnectionHandler._is_identity_confirmed(client, "different", None)
            is False
        )

    def test_no_identifiers_not_confirmed(self):
        client = ClientObj(uid="abc", ip_addresses=["10.0.0.5"])
        assert ConnectionHandler._is_identity_confirmed(client, None, None) is False


class TestIpAdmission:
    def test_same_uid_new_ip_is_registered(self):
        """The bug: same UID, different IP must be accepted and learned."""
        client = ClientObj(uid="abc", ip_addresses=["10.0.0.5"])
        assert _admission(client, "10.0.0.99", uid="abc", hostname=None) == "register"

    def test_known_ip_is_ok(self):
        client = ClientObj(uid="abc", ip_addresses=["10.0.0.5"])
        assert _admission(client, "10.0.0.5", uid="abc", hostname=None) == "ok"

    def test_hostname_only_client_learns_first_ip(self):
        client = ClientObj(uid="abc", hostname="host-a", ip_addresses=None)
        assert (
            _admission(client, "10.0.0.7", uid="abc", hostname="host-a")
            == "register"
        )

    def test_unknown_ip_without_identity_is_rejected(self):
        client = ClientObj(uid="abc", ip_addresses=["10.0.0.5"])
        assert (
            _admission(client, "10.0.0.99", uid=None, hostname=None) == "reject"
        )

    def test_add_ip_persists_new_address(self):
        client = ClientObj(uid="abc", ip_addresses=["10.0.0.5"])
        assert _admission(client, "10.0.0.99", uid="abc", hostname=None) == "register"
        client.add_ip("10.0.0.99")
        assert client.has_ip("10.0.0.99")
        assert "10.0.0.5" in client.ip_addresses
