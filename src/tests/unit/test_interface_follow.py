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

"""Following the selected interface across an address change.

``ServerConfig.host`` stores an address, which is readable in the config file
and stable on a static setup - but it is also exactly what a DHCP renewal
changes. When that happens the selection stops matching anything: normally the
server silently falls back to advertising every interface, and with
``host_exclusive`` it stops accepting connections altogether.

Following requires *evidence* that it is the same link. Two kinds are
accepted, and nothing else: the adapter the address used to sit on, and an
unambiguous subnet match. Guessing beyond that would move the server onto an
interface the admin never chose.
"""

import pytest

from utils.net import follow_interface_address
from utils.net._base import LocalInterface


def _iface(ip, name, prefix=24):
    net = ip.rsplit(".", 1)[0] + ".0"
    return LocalInterface(
        name=name,
        display_name=name,
        ip=ip,
        prefix=prefix,
        cidr=f"{net}/{prefix}",
        is_default_route=False,
    )


WIFI = _iface("192.168.1.20", "en0")
CABLE = _iface("10.0.0.1", "eth1")
CABLE_RENEWED = _iface("10.0.0.7", "eth1")


class TestNothingToDo:
    def test_address_still_present_is_left_alone(self):
        assert (
            follow_interface_address("10.0.0.1", [WIFI, CABLE], [WIFI, CABLE]) is None
        )

    @pytest.mark.parametrize("auto", [None, "", "0.0.0.0"])
    def test_auto_is_never_rewritten(self, auto):
        assert (
            follow_interface_address(auto, [WIFI, CABLE], [WIFI, CABLE_RENEWED]) is None
        )

    def test_a_preference_naming_an_adapter_needs_no_following(self):
        """Only addresses go stale; an adapter name already survives a renewal."""
        assert (
            follow_interface_address("eth1", [WIFI, CABLE], [WIFI, CABLE_RENEWED])
            is None
        )


class TestFollowsTheSameAdapter:
    def test_renewal_on_the_same_adapter_is_followed(self):
        """The daemon saw the address on eth1; eth1 now has another one."""
        assert (
            follow_interface_address("10.0.0.1", [WIFI, CABLE], [WIFI, CABLE_RENEWED])
            == "10.0.0.7"
        )

    def test_adapter_gone_is_not_followed(self):
        """A vanished cable must not drag the selection onto another link."""
        assert follow_interface_address("10.0.0.1", [WIFI, CABLE], [WIFI]) is None


class TestFollowsAnUnambiguousSubnet:
    """Covers the restart case: after a restart there is no previous snapshot,
    so the adapter link is gone and the subnet is the only evidence left."""

    def test_same_subnet_on_exactly_one_interface_is_followed(self):
        assert (
            follow_interface_address("10.0.0.1", None, [WIFI, CABLE_RENEWED])
            == "10.0.0.7"
        )

    def test_ambiguous_subnet_is_not_followed(self):
        """Two interfaces on that subnet: no evidence which one was meant."""
        twin = _iface("10.0.0.9", "eth2")

        assert follow_interface_address("10.0.0.1", None, [CABLE_RENEWED, twin]) is None

    def test_no_interface_on_that_subnet_is_not_followed(self):
        assert follow_interface_address("10.0.0.1", None, [WIFI]) is None

    def test_adapter_evidence_wins_over_subnet(self):
        """A VPN appearing on the same subnet must not steal the selection."""
        vpn = _iface("10.0.0.99", "utun3")

        assert (
            follow_interface_address(
                "10.0.0.1", [WIFI, CABLE], [WIFI, CABLE_RENEWED, vpn]
            )
            == "10.0.0.7"
        )


class TestDegenerateInput:
    def test_no_interfaces_at_all(self):
        assert follow_interface_address("10.0.0.1", [CABLE], []) is None

    def test_non_address_preference_is_ignored(self):
        assert follow_interface_address("Ethernet 1", [CABLE], [CABLE_RENEWED]) is None
