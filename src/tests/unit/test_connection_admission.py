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

import pytest

from model.client import ClientObj, ClientsManager
from network.connection.server import ConnectionHandler


def _admission(client, address, uid, hostname):
    """Mirror the decision made inline in ``_handshake``.

    Returns one of "ok" (IP already known), "register" (learn the new IP),
    or "reject".
    """
    if ConnectionHandler._check_client(client, address):
        return "ok"
    identity_confirmed = ConnectionHandler._is_identity_confirmed(client, uid, hostname)
    if client.ip_addresses and not identity_confirmed:
        return "reject"
    return "register"


class TestIsIdentityConfirmed:
    def test_matching_uid_confirms(self):
        client = ClientObj(uid="abc", ip_addresses=["10.0.0.5"])
        assert ConnectionHandler._is_identity_confirmed(client, "abc", None) is True

    def test_matching_hostname_confirms(self):
        client = ClientObj(uid="abc", ip_addresses=["10.0.0.5"], hostname="host-a")
        assert ConnectionHandler._is_identity_confirmed(client, None, "host-a") is True

    def test_mismatched_uid_not_confirmed(self):
        client = ClientObj(uid="abc", ip_addresses=["10.0.0.5"])
        assert (
            ConnectionHandler._is_identity_confirmed(client, "different", None) is False
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
            _admission(client, "10.0.0.7", uid="abc", hostname="host-a") == "register"
        )

    def test_unknown_ip_without_identity_is_rejected(self):
        client = ClientObj(uid="abc", ip_addresses=["10.0.0.5"])
        assert _admission(client, "10.0.0.99", uid=None, hostname=None) == "reject"

    def test_add_ip_persists_new_address(self):
        client = ClientObj(uid="abc", ip_addresses=["10.0.0.5"])
        assert _admission(client, "10.0.0.99", uid="abc", hostname=None) == "register"
        client.add_ip("10.0.0.99")
        assert client.has_ip("10.0.0.99")
        assert "10.0.0.5" in client.ip_addresses


class TestResolveClient:
    """Strong identity (UID -> hostname) must win over the IP-based prematch.

    The bug: a different machine reusing a stale IP (same IP, new
    hostname/UID) was force-matched to the record found by IP and then
    rejected by the UID consistency check.
    """

    def _manager(self):
        mgr = ClientsManager()
        mgr.add_client(
            ClientObj(uid="federico-uid", hostname="Federico", ip_addresses=["10.0.0.5"])
        )
        return mgr

    def test_same_ip_different_identity_is_new_client(self):
        """fede-udu reuses Federico's IP -> unknown, routed to approval."""
        mgr = self._manager()
        ip_prematch = mgr.get_client(ip_address="10.0.0.5")
        assert ip_prematch is not None
        resolved = ConnectionHandler._resolve_client(
            mgr, ip_prematch, uid="fede-udu-uid", hostname="fede-udu"
        )
        assert resolved is None

    def test_matching_uid_resolves_even_from_new_ip(self):
        mgr = self._manager()
        resolved = ConnectionHandler._resolve_client(
            mgr, None, uid="federico-uid", hostname="whatever"
        )
        assert resolved is not None
        assert resolved.uid == "federico-uid"

    def test_matching_hostname_resolves_without_uid(self):
        mgr = self._manager()
        resolved = ConnectionHandler._resolve_client(
            mgr, None, uid=None, hostname="Federico"
        )
        assert resolved is not None
        assert resolved.host_name == "Federico"

    def test_no_identity_keeps_ip_prematch(self):
        mgr = self._manager()
        ip_prematch = mgr.get_client(ip_address="10.0.0.5")
        resolved = ConnectionHandler._resolve_client(
            mgr, ip_prematch, uid=None, hostname=None
        )
        assert resolved is ip_prematch

    def test_legacy_ip_only_record_adopts_hostname(self):
        mgr = ClientsManager()
        legacy = ClientObj(ip_addresses=["10.0.0.5"])
        mgr.add_client(legacy)
        resolved = ConnectionHandler._resolve_client(
            mgr, legacy, uid="new-uid", hostname="new-host"
        )
        assert resolved is legacy
        assert resolved.host_name == "new-host"


class TestUpdateClient:
    """``update_client`` must match on strong identity (UID) and never clobber a
    different client that merely shares an IP or hostname.
    """

    def test_same_ip_does_not_clobber_other_uid(self):
        """The corruption: update_client(fede-udu) must not overwrite Federico,
        which shares the same IP but has a different UID."""
        mgr = ClientsManager()
        federico = ClientObj(uid="X", hostname="Federico", ip_addresses=["10.0.0.5"])
        fede_udu = ClientObj(uid="Y", hostname="fede-udu", ip_addresses=["10.0.0.5"])
        mgr.add_client(federico)
        mgr.add_client(fede_udu)

        updated = ClientObj(uid="Y", hostname="fede-udu", ip_addresses=["10.0.0.5"])
        mgr.update_client(updated)

        clients = mgr.get_clients()
        assert len(clients) == 2
        assert {c.uid for c in clients} == {"X", "Y"}
        # Federico is untouched.
        assert mgr.get_client(uid="X") is federico
        # fede-udu is the freshly updated object.
        assert mgr.get_client(uid="Y") is updated

    def test_update_unknown_uid_raises(self):
        mgr = ClientsManager()
        mgr.add_client(ClientObj(uid="X", hostname="Federico", ip_addresses=["10.0.0.5"]))
        with pytest.raises(ValueError):
            mgr.update_client(
                ClientObj(uid="Z", hostname="ghost", ip_addresses=["10.0.0.9"])
            )

    def test_anonymous_record_upgraded_by_ip(self):
        """A still-anonymous record (no UID) is upgraded when an update arrives
        with a UID and a matching IP."""
        mgr = ClientsManager()
        anon = ClientObj(ip_addresses=["10.0.0.5"])
        mgr.add_client(anon)
        upgraded = ClientObj(uid="Y", hostname="fede-udu", ip_addresses=["10.0.0.5"])
        mgr.update_client(upgraded)
        clients = mgr.get_clients()
        assert len(clients) == 1
        assert clients[0] is upgraded
        assert clients[0].uid == "Y"

    def test_match_by_hostname_without_uid(self):
        mgr = ClientsManager()
        original = ClientObj(hostname="host-a", ip_addresses=["10.0.0.5"])
        mgr.add_client(original)
        replacement = ClientObj(hostname="host-a", ip_addresses=["10.0.0.9"])
        mgr.update_client(replacement)
        clients = mgr.get_clients()
        assert len(clients) == 1
        assert clients[0] is replacement
