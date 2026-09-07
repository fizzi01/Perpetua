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

"""Multi-address discovery: A records plus the ``addresses`` TXT list.

A multi-homed server publishes every link it has. The A records only carry
the ones whose packets reached this client - and zeroconf hands them back in
receive order, so entry 0 is whatever arrived last, not what the server
prefers. The TXT list fills in the rest.
"""

from unittest.mock import AsyncMock, patch

import pytest

from service import Service, ServiceDiscovery, _ServiceListener


def _info(addresses=("10.0.0.1",), props=None, port=5555):
    info = AsyncMock()
    info.parsed_addresses = lambda: list(addresses)
    info.port = port
    info.properties = props if props is not None else {b"hostname": b"server-host"}
    return info


class TestServiceAddresses:
    def test_legacy_single_address_still_works(self):
        """A 1.6.0 server advertises one address and no TXT list."""
        svc = Service("n", "10.0.0.1", 5555)

        assert svc.addresses == ["10.0.0.1"]
        assert svc.address == "10.0.0.1"
        assert svc.as_dict()["address"] == "10.0.0.1"

    def test_as_dict_is_additive(self):
        """Old GUIs read "address"; "addresses" must not displace it."""
        svc = Service("n", "10.0.0.1", 5555, addresses=["10.0.0.1", "192.168.1.20"])

        data = svc.as_dict()

        assert data["address"] == "10.0.0.1"
        assert data["addresses"] == ["10.0.0.1", "192.168.1.20"]

    def test_address_setter_promotes_keeping_fallbacks(self):
        """update_service assigns .address; the others must survive as candidates."""
        svc = Service("n", "10.0.0.1", 5555, addresses=["10.0.0.1", "192.168.1.20"])

        svc.address = "192.168.1.20"

        assert svc.addresses == ["192.168.1.20", "10.0.0.1"]

    def test_constructor_deduplicates(self):
        svc = Service("n", "10.0.0.1", 5555, addresses=["10.0.0.1", "10.0.0.1"])

        assert svc.addresses == ["10.0.0.1"]

    def test_no_address_is_not_a_crash(self):
        svc = Service("n", "", 5555, addresses=[])

        assert svc.addresses == []
        assert svc.address == ""


class TestCandidateAddresses:
    def test_txt_entries_extend_the_a_records(self):
        info = _info(
            addresses=("10.0.0.1",),
            props={
                b"hostname": b"server-host",
                b"addresses": b"10.0.0.1,192.168.1.20,172.16.0.5",
            },
        )

        result = _ServiceListener._candidate_addresses(info)

        assert result == ["10.0.0.1", "192.168.1.20", "172.16.0.5"]

    def test_a_records_come_first(self):
        """The record that actually reached us is the best first guess."""
        info = _info(
            addresses=("192.168.1.20",),
            props={b"addresses": b"10.0.0.1,192.168.1.20"},
        )

        result = _ServiceListener._candidate_addresses(info)

        assert result[0] == "192.168.1.20"

    def test_no_txt_is_just_the_a_records(self):
        info = _info(addresses=("10.0.0.1", "192.168.1.20"), props={})

        assert _ServiceListener._candidate_addresses(info) == [
            "10.0.0.1",
            "192.168.1.20",
        ]

    @pytest.mark.parametrize("raw", [b"", b"   ", b",,,"])
    def test_blank_txt_is_ignored(self, raw):
        info = _info(addresses=("10.0.0.1",), props={b"addresses": raw})

        assert _ServiceListener._candidate_addresses(info) == ["10.0.0.1"]

    def test_txt_whitespace_is_trimmed_and_duplicates_dropped(self):
        info = _info(
            addresses=("10.0.0.1",),
            props={b"addresses": b" 10.0.0.1 , 192.168.1.20 "},
        )

        assert _ServiceListener._candidate_addresses(info) == [
            "10.0.0.1",
            "192.168.1.20",
        ]

    def test_undecodable_txt_does_not_break_discovery(self):
        info = _info(addresses=("10.0.0.1",), props={b"addresses": b"\xff\xfe"})

        assert _ServiceListener._candidate_addresses(info) == ["10.0.0.1"]


class TestTxtHostname:
    @pytest.mark.parametrize("raw", [None, b"", b"   "])
    def test_empty_hostname_becomes_none(self, raw):
        """An empty hostname is not a hostname: it poisons identity checks."""
        info = _info(props={b"hostname": raw} if raw is not None else {})

        assert _ServiceListener._txt_hostname(info) is None

    def test_hostname_is_decoded_and_trimmed(self):
        info = _info(props={b"hostname": b"  server-host  "})

        assert _ServiceListener._txt_hostname(info) == "server-host"


class TestListenerIntegration:
    @pytest.mark.anyio
    async def test_add_service_captures_every_candidate(self):
        listener = _ServiceListener()
        info = _info(
            addresses=("10.0.0.1",),
            props={
                b"hostname": b"server-host",
                b"addresses": b"10.0.0.1,192.168.1.20",
                b"pairing_port": b"5553",
            },
        )

        with patch("service.AsyncServiceInfo", return_value=info):
            await listener._service_info_task(None, "_t._tcp.local.", "uid1._t.local.")

        (svc,) = listener.get_services()
        assert svc.uid == "uid1"
        assert svc.addresses == ["10.0.0.1", "192.168.1.20"]
        assert svc.hostname == "server-host"
        assert svc.pairing_port == 5553

    @pytest.mark.anyio
    async def test_re_announcement_does_not_duplicate_the_server(self):
        """Duplicates skew the "exactly one server found" auto-selection."""
        listener = _ServiceListener()
        info = _info(addresses=("10.0.0.1",))

        with patch("service.AsyncServiceInfo", return_value=info):
            await listener._service_info_task(None, "_t._tcp.local.", "uid1._t.local.")
            await listener._service_info_task(None, "_t._tcp.local.", "uid1._t.local.")

        assert len(listener.get_services()) == 1

    @pytest.mark.anyio
    async def test_update_replaces_the_whole_address_set(self):
        """A withdrawn address must stop being a candidate."""
        listener = _ServiceListener()
        listener._services.append(
            Service(
                "uid1._t.local.",
                "10.0.0.1",
                5555,
                uid="uid1",
                addresses=["10.0.0.1", "172.16.0.5"],
            )
        )
        info = _info(addresses=("192.168.1.20",), props={b"addresses": b"192.168.1.20"})

        with patch("service.AsyncServiceInfo", return_value=info):
            await listener._service_info_update_task(
                None, "_t._tcp.local.", "uid1._t.local."
            )

        (svc,) = listener.get_services()
        assert svc.addresses == ["192.168.1.20"]

    @pytest.mark.anyio
    async def test_update_with_empty_hostname_keeps_the_good_one(self):
        listener = _ServiceListener()
        listener._services.append(
            Service("uid1._t.local.", "10.0.0.1", 5555, uid="uid1", hostname="known")
        )
        info = _info(addresses=("10.0.0.1",), props={b"hostname": b""})

        with patch("service.AsyncServiceInfo", return_value=info):
            await listener._service_info_update_task(
                None, "_t._tcp.local.", "uid1._t.local."
            )

        (svc,) = listener.get_services()
        assert svc.hostname == "known"


class TestRegistrationLogging:
    """The TXT map is caller-supplied and must not be splatted into the log.

    It carries an ``addresses`` key, which collided with the log's own
    ``addresses`` field and made every per-interface registration die with
    "got multiple values for keyword argument" - taking mDNS down entirely.
    """

    @pytest.mark.anyio
    async def test_per_interface_registration_survives_txt_keys(self, monkeypatch):
        registered = []

        class _FakeZeroconf:
            def __init__(self, interfaces=None):
                self.interfaces = interfaces

            async def async_register_service(self, info):
                registered.append(info)

            async def async_unregister_service(self, info):
                return None

            async def async_close(self):
                return None

        monkeypatch.setattr("service.AsyncZeroconf", _FakeZeroconf)
        discovery = ServiceDiscovery()

        await discovery.register_service(
            host="0.0.0.0",
            port=5555,
            uid="uid1",
            extra_props={"pairing_port": "5553", "addresses": "10.0.0.1,192.168.1.20"},
            interface_addresses=["10.0.0.1", "192.168.1.20"],
        )

        assert len(registered) == 2
        await discovery._unregister_iface_responders()

    @pytest.mark.anyio
    async def test_single_registration_survives_txt_keys(self, monkeypatch):
        """Same hazard on the fallback path, with an injected instance."""
        zc = AsyncMock()
        discovery = ServiceDiscovery(async_mdns=zc)

        await discovery.register_service(
            host="10.0.0.1",
            port=5555,
            uid="uid1",
            extra_props={"addresses": "10.0.0.1", "host": "shadow", "port": "9"},
        )

        zc.async_register_service.assert_awaited_once()


class TestServiceInfoSignature:
    """Guards the assumption the TXT fallback rests on."""

    def test_zeroconf_documents_receive_order_not_publish_order(self):
        """parsed_addresses is LIFO on the receiving side.

        This is *why* the candidate list exists: the publisher cannot control
        which address a client sees first, so ordering the A records would not
        steer an unupgraded client. Pinned here so the rationale does not get
        quietly invalidated by a zeroconf upgrade.
        """
        from zeroconf import ServiceInfo

        assert "most recently added" in (ServiceInfo.parsed_addresses.__doc__ or "")
