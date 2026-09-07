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


def _make_server(app_config, server_config, ssl: bool = False) -> Server:
    # SSL off by default: certificate behaviour is covered in
    # test_server_cert_multihomed and generating real keys is slow.
    if ssl:
        server_config.enable_ssl()
    else:
        server_config.disable_ssl()
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
    """The core invariant: every listener binds the wildcard, whatever ``host``
    says. ``host`` selects what is *advertised*.

    These drive the real ``start()`` rather than calling the helpers directly:
    asserting ``BIND_ALL == "0.0.0.0"`` and then invoking
    ``_is_port_available(BIND_ALL, ...)`` by hand proves nothing about what
    production passes, and a regression to ``config.host`` would slip through.
    """

    @pytest.mark.anyio
    async def test_listener_binds_the_wildcard_not_the_preference(
        self, app_config, server_config, monkeypatch
    ):
        captured = _stub_start(monkeypatch)
        server = _make_server(app_config, server_config)
        server.config.host = "10.99.99.99"  # deliberately not on this machine

        assert await server.start() is False  # stubbed handler refuses

        assert captured["connection_host"] == "0.0.0.0"

    @pytest.mark.anyio
    async def test_pairing_listener_also_binds_the_wildcard(
        self, app_config, server_config, monkeypatch
    ):
        """Otherwise pairing would be reachable on one interface while the
        data port listens on all - or the reverse.

        The handler is allowed to start so ``start()`` actually reaches the
        pairing call: asserting on a value this test passed in itself would
        prove nothing about production.
        """
        captured = _stub_start(monkeypatch, handler_starts=True)
        server_config.enable_ssl()
        server = _make_server(app_config, server_config, ssl=True)
        server.config.host = "10.99.99.99"

        assert await server.start() is True
        await server.stop(True)

        assert "pairing_host" in captured, "start() never reached the pairing listener"
        assert captured["pairing_host"] == "0.0.0.0"

    @pytest.mark.anyio
    async def test_port_probe_uses_the_wildcard(
        self, app_config, server_config, monkeypatch
    ):
        """Probing ``config.host`` reported EADDRNOTAVAIL as a port conflict:
        an absent interface made the server refuse to start complaining about
        a port that was in fact free."""
        captured = _stub_start(monkeypatch)
        server = _make_server(app_config, server_config)
        server.config.host = "10.99.99.99"

        await server.start()

        assert captured["probed_hosts"] == ["0.0.0.0"]

    @pytest.mark.anyio
    async def test_start_succeeds_with_an_absent_preference(
        self, app_config, server_config, monkeypatch
    ):
        """The whole point of not binding the preference: a stale choice must
        never stop the server from coming up."""
        _stub_start(monkeypatch, handler_starts=True)
        server = _make_server(app_config, server_config)
        server.config.host = "10.99.99.99"

        assert await server.start() is True
        await server.stop(True)


class TestStartWiring:
    """Things start() must hand to the layers below.

    Unit-testing the helpers is not enough: if production stops calling them
    the feature is dead and every isolated test still passes.
    """

    @pytest.mark.anyio
    async def test_accept_filter_is_wired_into_the_listener(
        self, app_config, server_config, monkeypatch
    ):
        """Without this, ``host_exclusive`` is a checkbox that does nothing."""
        captured = _stub_start(monkeypatch)
        server = _make_server(app_config, server_config)

        await server.start()

        assert captured["interface_filter"] == server._on_interface_accept

    @pytest.mark.anyio
    async def test_san_is_rechecked_at_start_even_with_certs_loaded(
        self, app_config, server_config, monkeypatch
    ):
        """Certificates are set up in __init__, i.e. at SERVICE_CHOICE time.

        The address set can change before Start (a cable plugged in meanwhile),
        and the ``not self.certfile`` guard skips the whole certificate block -
        so without an explicit re-check the server would advertise an address
        its leaf does not cover.
        """
        _stub_start(monkeypatch)
        server_config.enable_ssl()
        server = _make_server(app_config, server_config)
        server_config.enable_ssl()
        server.certfile, server.keyfile = "cert.pem", "key.pem"  # already loaded

        checked = []
        monkeypatch.setattr(
            Server,
            "_reissue_server_cert_if_ip_changed",
            lambda self: checked.append(True),
        )

        await server.start()

        assert checked, "SAN check skipped at start"


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


def _stub_start(monkeypatch, handler_starts: bool = False) -> dict:
    """Let ``Server.start()`` run far enough to record what it binds.

    Input capture, mDNS and the real socket are stubbed; the connection
    handler is a recorder so the host it is constructed with can be asserted.
    """
    captured: dict = {"probed_hosts": []}

    monkeypatch.setattr(
        Server,
        "_is_port_available",
        staticmethod(lambda host, port: captured["probed_hosts"].append(host) or True),
    )
    monkeypatch.setattr(
        "service.server.list_local_interfaces_async", _async_returning(TWO_LINKS)
    )
    monkeypatch.setattr(Server, "_initialize_streams", _async_returning(None))
    monkeypatch.setattr(Server, "_initialize_components", _async_returning(None))
    monkeypatch.setattr(
        Server, "_reconcile_layouts_with_monitors", _async_returning([])
    )

    async def _pairing(self, host=None, port=None):
        captured["pairing_host"] = host
        return True

    monkeypatch.setattr(Server, "start_pairing_service", _pairing)

    class _Handler:
        def __init__(self, **kwargs):
            captured["connection_host"] = kwargs.get("host")
            captured["connection_port"] = kwargs.get("port")
            captured["interface_filter"] = kwargs.get("interface_filter")

        async def start(self):
            return handler_starts

        def set_server_uid(self, uid):
            captured["handler_uid"] = uid

        def invalidate_ssl_context(self):
            captured["ssl_invalidated"] = True

        async def stop(self, *a, **k):
            return None

    monkeypatch.setattr("service.server.ConnectionHandler", _Handler)
    return captured


def _async_returning(value):
    async def _inner(*_args, **_kwargs):
        return value

    return _inner


def _async_recording(sink):
    async def _inner(*args, **kwargs):
        sink.append((args, kwargs))

    return _inner
