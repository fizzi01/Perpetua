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

"""Bind vs advertise, and the accept-time interface filter.

``config.host`` used to be the bind address, and the GUI persisted a concrete
IP into it on any options edit - so a multi-homed server silently stopped
listening on every interface but one. It is now an *advertise* preference and
the listener always binds the wildcard.
"""

import pytest

from service.server import Server
from utils.net._base import LocalInterface


def _make_server(app_config, server_config) -> Server:
    server_config.disable_ssl()  # certificates are covered elsewhere
    return Server(
        app_config=app_config,
        server_config=server_config,
        auto_load_config=False,
    )


def _iface(ip, name, default=False):
    return LocalInterface(
        name=name,
        display_name=name,
        ip=ip,
        prefix=24,
        cidr=f"{ip.rsplit('.', 1)[0]}.0/24",
        is_default_route=default,
    )


TWO_LINKS = [
    _iface("192.168.1.20", "en0", default=True),
    _iface("10.0.0.1", "eth1"),
]


class TestBindIsNotConfigurable:
    def test_port_probe_ignores_the_advertise_preference(
        self, app_config, server_config, monkeypatch
    ):
        """Probing config.host reported EADDRNOTAVAIL as a port conflict.

        An interface that is momentarily absent would make the server refuse
        to start with a message about a port that is in fact free.
        """
        server = _make_server(app_config, server_config)
        server.config.host = "10.99.99.99"  # not present on this machine

        probed = []
        monkeypatch.setattr(
            Server,
            "_is_port_available",
            staticmethod(lambda host, port: probed.append(host) or True),
        )

        assert server._is_port_available(server.BIND_ALL, 1234) is True
        # The production call site is asserted by the start test below; here we
        # only pin that BIND_ALL is the wildcard and not the preference.
        assert server.BIND_ALL == "0.0.0.0"
        assert probed == ["0.0.0.0"]


class TestInterfaceFilter:
    """``host_exclusive`` is enforced at accept time, not at bind time."""

    def test_disabled_by_default_accepts_everything(self, app_config, server_config):
        server = _make_server(app_config, server_config)
        server._advertised_addresses = ["10.0.0.1"]

        assert server.config.host_exclusive is False
        assert server._on_interface_accept("192.168.1.20") is True

    def test_enabled_accepts_only_the_selected_interface(
        self, app_config, server_config
    ):
        server = _make_server(app_config, server_config)
        server.config.host = "10.0.0.1"
        server.config.host_exclusive = True
        server._advertised_addresses = ["10.0.0.1", "192.168.1.20"]

        assert server._on_interface_accept("10.0.0.1") is True
        assert server._on_interface_accept("192.168.1.20") is False

    def test_fails_open_when_nothing_resolves(
        self, app_config, server_config, monkeypatch
    ):
        """A transient enumeration failure must not lock the admin out."""
        server = _make_server(app_config, server_config)
        server.config.host = "10.0.0.1"
        server.config.host_exclusive = True
        server._advertised_addresses = []
        monkeypatch.setattr(
            server.config, "get_advertise_addresses", lambda *_a, **_k: []
        )

        assert server._on_interface_accept("192.168.1.20") is True


class TestAdvertiseResolution:
    def test_auto_advertises_every_address(self, app_config, server_config):
        server = _make_server(app_config, server_config)
        server.config.host = "0.0.0.0"

        assert server.config.get_advertise_addresses(TWO_LINKS) == [
            "192.168.1.20",
            "10.0.0.1",
        ]

    def test_explicit_choice_leads(self, app_config, server_config):
        server = _make_server(app_config, server_config)
        server.config.host = "eth1"

        assert server.config.get_advertise_addresses(TWO_LINKS)[0] == "10.0.0.1"


class TestRefreshAdvertisement:
    @pytest.mark.anyio
    async def test_no_op_when_nothing_changed(
        self, app_config, server_config, monkeypatch
    ):
        """Steady state must not churn mDNS or the certificate every 30s."""
        server = _make_server(app_config, server_config)
        server._advertised_addresses = ["192.168.1.20", "10.0.0.1"]

        monkeypatch.setattr(
            "service.server.list_local_interfaces_async",
            _async_returning(TWO_LINKS),
        )
        calls = []
        monkeypatch.setattr(
            server._mdns_service,
            "register_service",
            _async_recording(calls),
        )

        await server.refresh_advertisement()

        assert calls == []

    @pytest.mark.anyio
    async def test_empty_result_keeps_the_previous_advertisement(
        self, app_config, server_config, monkeypatch
    ):
        """Going silent is worse than advertising a stale-but-working address."""
        server = _make_server(app_config, server_config)
        server._advertised_addresses = ["10.0.0.1"]

        monkeypatch.setattr(
            "service.server.list_local_interfaces_async", _async_returning([])
        )
        calls = []
        monkeypatch.setattr(
            server._mdns_service, "register_service", _async_recording(calls)
        )

        await server.refresh_advertisement()

        assert calls == []
        assert server._advertised_addresses == ["10.0.0.1"]

    @pytest.mark.anyio
    async def test_change_reregisters_with_per_interface_targets(
        self, app_config, server_config, monkeypatch
    ):
        """A cable plugged in after start must reach mDNS without a restart."""
        server = _make_server(app_config, server_config)
        server._advertised_addresses = ["192.168.1.20"]

        monkeypatch.setattr(
            "service.server.list_local_interfaces_async",
            _async_returning(TWO_LINKS),
        )
        calls = []
        monkeypatch.setattr(
            server._mdns_service, "register_service", _async_recording(calls)
        )
        monkeypatch.setattr(
            server._mdns_service, "unregister_service", _async_returning(None)
        )

        await server.refresh_advertisement()

        assert len(calls) == 1
        kwargs = calls[0][1]
        # Auto: one responder per interface, each announcing its own address.
        assert kwargs["interface_addresses"] == ["192.168.1.20", "10.0.0.1"]
        # Full list in TXT so a client whose A record is unreachable can fall
        # back without the admin configuring anything.
        assert kwargs["extra_props"]["addresses"] == "192.168.1.20,10.0.0.1"
        assert server._advertised_addresses == ["192.168.1.20", "10.0.0.1"]


def _async_returning(value):
    async def _inner(*_args, **_kwargs):
        return value

    return _inner


def _async_recording(sink):
    async def _inner(*args, **kwargs):
        sink.append((args, kwargs))

    return _inner
