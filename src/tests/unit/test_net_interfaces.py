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

"""Interface enumeration and advertise-address resolution.

These back the multi-homing fix: on a machine with several NICs the address
a server announces used to be whichever one reached the internet, which on a
direct cable is always the wrong one.
"""

from types import SimpleNamespace

import pytest

from utils import net as net_module
from utils.net import (
    MissingIpError,
    resolve_advertise_addresses,
    resolve_advertise_interfaces,
)
from utils.net._base import CommonNetInfo, LocalInterface


def _ip(addr, prefix=24, is_v4=True):
    return SimpleNamespace(ip=addr, network_prefix=prefix, is_IPv4=is_v4)


def _adapter(name, ips, nice_name=None):
    return SimpleNamespace(name=name, nice_name=nice_name or name, ips=ips)


@pytest.fixture
def fake_adapters(monkeypatch):
    """Install a fake ifaddr adapter list; returns the setter."""

    def _install(adapters):
        monkeypatch.setattr(
            net_module._base.ifaddr, "get_adapters", lambda: list(adapters)
        )

    return _install


def _iface(ip, name="eth0", display=None, prefix=24, default=False):
    return LocalInterface(
        name=name,
        display_name=display or name,
        ip=ip,
        prefix=prefix,
        cidr=f"{ip.rsplit('.', 1)[0]}.0/{prefix}",
        is_default_route=default,
    )


# ---------------------------------------------------------------- enumeration


def test_ipv6_entries_are_skipped(fake_adapters):
    """ifaddr yields a tuple for IPv6; only the IPv4 string is advertisable."""
    fake_adapters(
        [
            _adapter(
                "en0",
                [
                    _ip(("fe80::1", 0, 1), prefix=64, is_v4=False),
                    _ip("192.168.1.10"),
                ],
            )
        ]
    )

    result = CommonNetInfo.list_local_interfaces(default_route_ip=None)

    assert [i.ip for i in result] == ["192.168.1.10"]


@pytest.mark.parametrize("rejected", ["127.0.0.1", "169.254.3.4", "0.0.0.0"])
def test_unusable_addresses_are_filtered(fake_adapters, rejected):
    """Filtering delegates to _is_usable_ip - no second copy of the rules."""
    fake_adapters([_adapter("x", [_ip(rejected)])])

    assert CommonNetInfo.list_local_interfaces(default_route_ip=None) == []


def test_include_unusable_keeps_link_local(fake_adapters):
    """The picker needs them: a direct cable with no DHCP lands on 169.254/16."""
    fake_adapters([_adapter("eth1", [_ip("169.254.7.7", prefix=16)])])

    result = CommonNetInfo.list_local_interfaces(
        include_unusable=True, default_route_ip=None
    )

    assert [i.ip for i in result] == ["169.254.7.7"]


def test_one_record_per_address(fake_adapters):
    """An adapter can hold several addresses; the user picks a link, not a NIC."""
    fake_adapters([_adapter("en0", [_ip("192.168.1.10"), _ip("10.0.0.1")])])

    result = CommonNetInfo.list_local_interfaces(default_route_ip=None)

    assert sorted(i.ip for i in result) == ["10.0.0.1", "192.168.1.10"]
    assert {i.name for i in result} == {"en0"}


def test_cidr_is_derived_from_prefix(fake_adapters):
    fake_adapters([_adapter("en0", [_ip("192.168.50.7", prefix=24)])])

    (iface,) = CommonNetInfo.list_local_interfaces(default_route_ip=None)

    assert iface.cidr == "192.168.50.0/24"


def test_default_route_sorts_first(fake_adapters):
    fake_adapters(
        [
            _adapter("zzz", [_ip("10.0.0.1")]),
            _adapter("aaa", [_ip("192.168.1.10")]),
        ]
    )

    result = CommonNetInfo.list_local_interfaces(default_route_ip="10.0.0.1")

    assert [i.ip for i in result] == ["10.0.0.1", "192.168.1.10"]
    assert result[0].is_default_route is True
    assert result[1].is_default_route is False


def test_enumeration_survives_missing_default_route(fake_adapters, monkeypatch):
    """An offline machine still has interfaces; nothing is flagged, nothing raises."""
    fake_adapters([_adapter("en0", [_ip("192.168.1.10")])])
    monkeypatch.setattr(
        CommonNetInfo,
        "get_local_ip",
        staticmethod(
            lambda *_a, **_k: (_ for _ in ()).throw(MissingIpError("offline"))
        ),
    )

    result = CommonNetInfo.list_local_interfaces()

    assert [i.ip for i in result] == ["192.168.1.10"]
    assert result[0].is_default_route is False


def test_enumeration_failure_returns_empty(monkeypatch):
    """A broken enumeration must never stop the server from starting."""

    def _boom():
        raise OSError("iphlpapi exploded")

    monkeypatch.setattr(net_module._base.ifaddr, "get_adapters", _boom)

    assert CommonNetInfo.list_local_interfaces(default_route_ip=None) == []


def test_to_dict_is_json_safe(fake_adapters):
    fake_adapters([_adapter("en0", [_ip("192.168.1.10")], nice_name="Ethernet 1")])

    (iface,) = CommonNetInfo.list_local_interfaces(default_route_ip="192.168.1.10")

    assert iface.to_dict() == {
        "name": "en0",
        "display_name": "Ethernet 1",
        "ip": "192.168.1.10",
        "prefix": 24,
        "cidr": "192.168.1.0/24",
        "is_default_route": True,
    }


# ------------------------------------------------------- advertise resolution


@pytest.fixture
def two_links():
    return [
        _iface("192.168.1.20", name="en0", display="Wi-Fi", default=True),
        _iface("10.0.0.1", name="eth1", display="Ethernet 1"),
    ]


@pytest.mark.parametrize("auto", [None, "", "0.0.0.0"])
def test_auto_advertises_everything(two_links, auto):
    """ "0.0.0.0" is the value in every untouched 1.6.0 config: it must mean "all"."""
    assert resolve_advertise_addresses(auto, two_links) == ["192.168.1.20", "10.0.0.1"]


@pytest.mark.parametrize("pref", ["eth1", "10.0.0.1", "Ethernet 1", "ethernet 1"])
def test_preference_matches_name_ip_or_display_name(two_links, pref):
    """Hand-edited configs and configs copied between machines both work."""
    assert resolve_advertise_addresses(pref, two_links)[0] == "10.0.0.1"


def test_chosen_first_but_others_kept(two_links):
    """The rest still reach the SAN and the TXT, so no address is unreachable."""
    assert resolve_advertise_addresses("eth1", two_links) == [
        "10.0.0.1",
        "192.168.1.20",
    ]


def test_stale_preference_falls_back_to_auto(two_links):
    """Cable unplugged: advertise too much rather than becoming invisible."""
    assert resolve_advertise_addresses("eth99", two_links) == [
        "192.168.1.20",
        "10.0.0.1",
    ]


def test_stale_preference_advertises_nothing_when_exclusive(two_links):
    """Falling back would invite clients to an address they are refused.

    With ``host_exclusive`` the accept filter turns away every interface while
    the chosen one is absent, so announcing the others contradicts it.
    """
    assert resolve_advertise_addresses("eth99", two_links, exclusive=True) == []


def test_matched_preference_advertises_only_itself_when_exclusive(two_links):
    assert resolve_advertise_addresses("eth1", two_links, exclusive=True) == [
        "10.0.0.1"
    ]


def test_no_usable_address_yields_empty():
    assert resolve_advertise_addresses("eth1", []) == []


def test_addresses_are_deduplicated():
    dupes = [_iface("10.0.0.1", name="a"), _iface("10.0.0.1", name="b")]

    assert resolve_advertise_addresses(None, dupes) == ["10.0.0.1"]


# --------------------------------------------- per-interface responder targets


def test_auto_speaks_on_every_interface(two_links):
    assert resolve_advertise_interfaces(None, two_links) == [
        "192.168.1.20",
        "10.0.0.1",
    ]


def test_explicit_choice_speaks_only_there(two_links):
    """ "Advertise on: eth1" must not keep announcing on Wi-Fi as well."""
    assert resolve_advertise_interfaces("eth1", two_links) == ["10.0.0.1"]


def test_stale_choice_still_speaks_somewhere(two_links):
    assert resolve_advertise_interfaces("eth99", two_links) == [
        "192.168.1.20",
        "10.0.0.1",
    ]


@pytest.mark.anyio
async def test_async_variant_matches_sync(fake_adapters):
    """Coroutines must use this one: enumeration blocks on Windows."""
    fake_adapters([_adapter("en0", [_ip("192.168.1.10")])])

    result = await net_module.list_local_interfaces_async()

    assert [i.ip for i in result] == ["192.168.1.10"]
