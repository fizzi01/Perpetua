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

"""What the server binds, and what it advertises as a consequence.

``config.host`` is the bind address. "0.0.0.0" - the default - listens on
every interface and advertises them all; a concrete address listens only
there and advertises only that. There is no separate advertise setting: the
two cannot disagree because one is derived from the other.
"""

import pytest

from service.server import Server, ServerStartError
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


class TestBind:
    """``host`` reaches the socket, unchanged.

    These drive the real ``start()`` rather than calling the helpers directly:
    a regression that resolved the address somewhere in between would still
    pass every isolated test of the helper.
    """

    @pytest.mark.anyio
    async def test_default_binds_every_interface(
        self, app_config, server_config, monkeypatch
    ):
        captured = _stub_start(monkeypatch)
        server = _make_server(app_config, server_config)

        await server.start()

        assert server.config.host == "0.0.0.0"
        assert captured["connection_host"] == "0.0.0.0"

    @pytest.mark.anyio
    async def test_chosen_address_reaches_the_listener(
        self, app_config, server_config, monkeypatch
    ):
        """The whole point of the picker: pick 10.0.0.1 and the server is on
        10.0.0.1, not on whichever interface holds the default route."""
        captured = _stub_start(monkeypatch)
        server = _make_server(app_config, server_config)
        server.config.host = "10.0.0.1"

        await server.start()

        assert captured["connection_host"] == "10.0.0.1"

    @pytest.mark.anyio
    async def test_pairing_listener_binds_the_same_address(
        self, app_config, server_config, monkeypatch
    ):
        """Otherwise pairing stays reachable everywhere while the data port
        does not, which is the confusing half-open state.

        The handler is allowed to start so ``start()`` actually reaches the
        pairing call: asserting a value this test passed in itself would prove
        nothing about production.
        """
        captured = _stub_start(monkeypatch, handler_starts=True)
        server_config.enable_ssl()
        server = _make_server(app_config, server_config, ssl=True)
        server.config.host = "10.0.0.1"

        assert await server.start() is True
        await server.stop(True)

        assert "pairing_host" in captured, "start() never reached the pairing listener"
        assert captured["pairing_host"] == "10.0.0.1"

    @pytest.mark.anyio
    async def test_port_probe_uses_the_bind_address(
        self, app_config, server_config, monkeypatch
    ):
        """Probing anything else makes the check meaningless: the port can be
        free on the wildcard and taken on the address we are about to use."""
        captured = _stub_start(monkeypatch)
        server = _make_server(app_config, server_config)
        server.config.host = "10.0.0.1"

        await server.start()

        assert captured["probed_hosts"] == ["10.0.0.1"]


class TestBindFailuresAreHonest:
    """A pinned address that is gone must say so.

    Both cases came back as "Port already in use", which sent the admin
    changing a port that was never the problem.
    """

    @pytest.mark.anyio
    async def test_absent_address_reports_itself(
        self, app_config, server_config, monkeypatch
    ):
        _stub_start(monkeypatch, bind_result="address_unavailable")
        server = _make_server(app_config, server_config)
        server.config.host = "10.99.99.99"

        with pytest.raises(ServerStartError) as excinfo:
            await server.start()

        assert excinfo.value.reason == "address_unavailable"
        assert "10.99.99.99" in str(excinfo.value)

    @pytest.mark.anyio
    async def test_taken_port_still_reports_the_port(
        self, app_config, server_config, monkeypatch
    ):
        _stub_start(monkeypatch, bind_result="port_in_use")
        server = _make_server(app_config, server_config)

        with pytest.raises(ServerStartError) as excinfo:
            await server.start()

        assert excinfo.value.reason == "port_in_use"

    def test_the_two_are_distinguished_at_the_socket(self, app_config, server_config):
        """Not a mock: the classification rests on errno, so it is worth
        checking against a real kernel."""
        import socket

        server = _make_server(app_config, server_config)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as taken:
            taken.bind(("127.0.0.1", 0))
            taken.listen()
            port = taken.getsockname()[1]

            assert server._probe_bind("127.0.0.1", port) == "port_in_use"
            assert server._probe_bind("10.99.99.99", port) == "address_unavailable"
            assert server._probe_bind("127.0.0.1", 0) is None


class TestAdvertisingFollowsTheBind:
    def test_wildcard_advertises_every_usable_address(self, app_config, server_config):
        server = _make_server(app_config, server_config)
        server.config.host = "0.0.0.0"

        assert server.config.get_advertise_addresses(TWO_LINKS) == [
            "192.168.1.20",
            "10.0.0.1",
        ]

    def test_a_pinned_address_advertises_only_itself(self, app_config, server_config):
        """Advertising anything else would invite clients to an address the
        socket is not listening on."""
        server = _make_server(app_config, server_config)
        server.config.host = "10.0.0.1"

        assert server.config.get_advertise_addresses(TWO_LINKS) == ["10.0.0.1"]

    def test_wildcard_never_advertises_loopback(self, app_config, server_config):
        """Automatic selection filters; it has no way to know loopback was
        wanted, and a client on another machine cannot use it."""
        server = _make_server(app_config, server_config)
        server.config.host = "0.0.0.0"
        ifaces = TWO_LINKS + [_iface("127.0.0.1", "lo0")]

        assert "127.0.0.1" not in server.config.get_advertise_addresses(ifaces)

    def test_a_pinned_loopback_is_advertised(self, app_config, server_config):
        """An explicit pick is intent, and the bind succeeded, so the address
        exists. Filtering it here would advertise nothing at all."""
        server = _make_server(app_config, server_config)
        server.config.host = "127.0.0.1"

        assert server.config.get_advertise_addresses(TWO_LINKS) == ["127.0.0.1"]

    def test_resolution_needs_no_enumeration_when_pinned(
        self, app_config, server_config, monkeypatch
    ):
        """The bind already proved the address is present; going back to the
        adapter list to confirm it is what used to lose the choice whenever
        enumeration disagreed."""
        server = _make_server(app_config, server_config)
        server.config.host = "10.0.0.1"

        def _boom(*_a, **_k):
            raise AssertionError("enumerated while pinned")

        monkeypatch.setattr("utils.net.list_local_interfaces", _boom)

        assert server.config.get_advertise_addresses() == ["10.0.0.1"]


class TestStartWiring:
    """Things start() must hand to the layers below. Unit-testing the helpers
    is not enough: if production stops calling them the feature is dead and
    every isolated test still passes."""

    @pytest.mark.anyio
    async def test_mdns_speaks_only_where_the_socket_listens(
        self, app_config, server_config, monkeypatch
    ):
        """One responder per advertised address, so a client on a given link
        receives an address reachable *on that link* with no TXT parsing."""
        captured = _stub_start(monkeypatch, handler_starts=True)
        server = _make_server(app_config, server_config)
        server.config.host = "10.0.0.1"

        assert await server.start() is True
        await server.stop(True)

        kwargs = captured["mdns"][0][1]
        assert kwargs["interface_addresses"] == ["10.0.0.1"]
        assert kwargs["extra_props"]["addresses"] == "10.0.0.1"
        assert kwargs["host"] == "10.0.0.1"

    @pytest.mark.anyio
    async def test_wildcard_registers_a_responder_per_interface(
        self, app_config, server_config, monkeypatch
    ):
        captured = _stub_start(monkeypatch, handler_starts=True)
        server = _make_server(app_config, server_config)

        assert await server.start() is True
        await server.stop(True)

        kwargs = captured["mdns"][0][1]
        assert kwargs["interface_addresses"] == ["192.168.1.20", "10.0.0.1"]
        assert kwargs["extra_props"]["addresses"] == "192.168.1.20,10.0.0.1"

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
    async def test_a_new_cable_is_announced_without_a_restart(
        self, app_config, server_config, monkeypatch
    ):
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
        assert kwargs["interface_addresses"] == ["192.168.1.20", "10.0.0.1"]
        assert kwargs["extra_props"]["addresses"] == "192.168.1.20,10.0.0.1"
        assert server._advertised_addresses == ["192.168.1.20", "10.0.0.1"]

    @pytest.mark.anyio
    async def test_a_pinned_bind_ignores_a_new_cable(
        self, app_config, server_config, monkeypatch
    ):
        """The socket is not listening there, so announcing it would be a lie.

        This is the property that makes the picker safe to leave alone: with
        an explicit address the advertisement is fixed for the lifetime of the
        listener and nothing can drift onto another link.
        """
        server = _make_server(app_config, server_config)
        server.config.host = "192.168.1.20"
        server._advertised_addresses = ["192.168.1.20"]

        monkeypatch.setattr(
            "service.server.list_local_interfaces_async",
            _async_returning(TWO_LINKS),
        )
        calls = []
        monkeypatch.setattr(
            server._mdns_service, "register_service", _async_recording(calls)
        )

        await server.refresh_advertisement()

        assert calls == []
        assert server._advertised_addresses == ["192.168.1.20"]


def _stub_start(monkeypatch, handler_starts: bool = False, bind_result=None) -> dict:
    """Let ``Server.start()`` run far enough to record what it binds.

    Input capture, mDNS and the real socket are stubbed; the connection
    handler is a recorder so the host it is constructed with can be asserted.
    """
    captured: dict = {"probed_hosts": [], "mdns": []}

    monkeypatch.setattr(
        Server,
        "_probe_bind",
        staticmethod(
            lambda host, port: captured["probed_hosts"].append(host) or bind_result
        ),
    )
    monkeypatch.setattr(
        "service.server.list_local_interfaces_async", _async_returning(TWO_LINKS)
    )
    # Class-level: start() builds its own ServiceDiscovery, so there is no
    # instance to patch before the call under test runs.
    monkeypatch.setattr(
        "service.ServiceDiscovery.register_service", _async_recording(captured["mdns"])
    )
    monkeypatch.setattr(
        "service.ServiceDiscovery.unregister_service", _async_returning(None)
    )
    monkeypatch.setattr("service.ServiceDiscovery.get_uid", lambda self: "uid-test")
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
