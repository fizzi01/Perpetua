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

"""Reachability-gated address selection on the client.

Discovery used to overwrite the persisted server address with whatever mDNS
reported, unprobed. On a multi-homed server that silently replaced a working
address with an unreachable one - and since the write was persisted, the setup
stayed broken across restarts. The rule these tests pin is: **a reachable
configuration is never touched.**
"""

import asyncio

import pytest

from service import Service
from service.client import Client


@pytest.fixture
async def client(app_config, client_config):
    """Async on purpose: Client.__init__ builds Futures/Locks that must bind
    to the loop the test runs on."""
    return Client(
        app_config=app_config,
        client_config=client_config,
        auto_load_config=False,
    )


@pytest.fixture
def reachable(client, monkeypatch):
    """Make _probe_tcp answer from a set of live addresses; records the calls."""
    calls: list[tuple[str, int]] = []
    live: set[str] = set()

    async def _probe(host, port, timeout=None):
        calls.append((host, port))
        return host in live

    monkeypatch.setattr(client, "_probe_tcp", _probe)

    class _Control:
        probes = calls

        @staticmethod
        def set_live(*addresses):
            live.clear()
            live.update(addresses)

    return _Control


def _svc(uid="srv-1", addresses=("10.0.0.1",), port=5555, hostname="server-host"):
    return Service(
        f"{uid}._perpetua._tcp.local.",
        addresses[0],
        port,
        uid=uid,
        hostname=hostname,
        addresses=list(addresses),
    )


class TestReconcileKeepsWorkingConfig:
    @pytest.mark.anyio
    async def test_saved_address_still_advertised_costs_no_probe(
        self, client, client_config, reachable
    ):
        """Steady state: nothing to decide, so nothing is probed."""
        client_config.set_server_connection(
            uid="srv-1", host="10.0.0.1", port=5555, hostname="server-host"
        )
        reachable.set_live("10.0.0.1")

        await client._reconcile_saved_server(
            "srv-1", ["10.0.0.1", "192.168.1.20"], 5555, "server-host"
        )

        assert reachable.probes == []
        assert client_config.get_server_host() == "10.0.0.1"

    @pytest.mark.anyio
    async def test_unadvertised_but_reachable_address_is_kept(
        self, client, client_config, reachable
    ):
        """The requirement: a working address survives even if mDNS dropped it.

        This is the exact case the old code destroyed - it saw "not advertised"
        and overwrote, without ever checking whether the address worked.
        """
        client_config.set_server_connection(
            uid="srv-1", host="10.0.0.1", port=5555, hostname="server-host"
        )
        reachable.set_live("10.0.0.1", "192.168.1.20")

        await client._reconcile_saved_server(
            "srv-1", ["192.168.1.20"], 5555, "server-host"
        )

        assert reachable.probes == [("10.0.0.1", 5555)]
        assert client_config.get_server_host() == "10.0.0.1"


class TestReconcileRetargets:
    @pytest.mark.anyio
    async def test_dead_saved_address_moves_to_a_live_candidate(
        self, client, client_config, reachable
    ):
        client_config.set_server_connection(
            uid="srv-1", host="172.16.0.9", port=5555, hostname="server-host"
        )
        reachable.set_live("192.168.1.20")

        await client._reconcile_saved_server(
            "srv-1", ["10.0.0.1", "192.168.1.20"], 5555, "server-host"
        )

        assert client_config.get_server_host() == "192.168.1.20"

    @pytest.mark.anyio
    async def test_candidate_order_decides_not_probe_completion_order(
        self, client, client_config, reachable
    ):
        """Probes run concurrently, so the winner must be the preferred one."""
        client_config.set_server_connection(
            uid="srv-1", host="172.16.0.9", port=5555, hostname="server-host"
        )
        reachable.set_live("10.0.0.1", "192.168.1.20")

        await client._reconcile_saved_server(
            "srv-1", ["10.0.0.1", "192.168.1.20"], 5555, "server-host"
        )

        assert client_config.get_server_host() == "10.0.0.1"

    @pytest.mark.anyio
    async def test_nothing_reachable_leaves_config_untouched(
        self, client, client_config, reachable
    ):
        """Better a stale target the retry loop can keep trying than a wrong one."""
        client_config.set_server_connection(
            uid="srv-1", host="172.16.0.9", port=5555, hostname="server-host"
        )
        reachable.set_live()

        await client._reconcile_saved_server(
            "srv-1", ["10.0.0.1", "192.168.1.20"], 5555, "server-host"
        )

        assert client_config.get_server_host() == "172.16.0.9"

    @pytest.mark.anyio
    async def test_empty_hostname_is_never_persisted(
        self, client, client_config, reachable
    ):
        """update_service can report b"" - an empty hostname poisons identity."""
        client_config.set_server_connection(
            uid="srv-1", host="172.16.0.9", port=5555, hostname="server-host"
        )
        reachable.set_live("10.0.0.1")

        await client._reconcile_saved_server("srv-1", ["10.0.0.1"], 5555, "")

        assert client_config.get_server_hostname() == "server-host"

    @pytest.mark.anyio
    async def test_budget_overrun_leaves_config_untouched(
        self, client, client_config, monkeypatch
    ):
        client_config.set_server_connection(
            uid="srv-1", host="172.16.0.9", port=5555, hostname="server-host"
        )
        monkeypatch.setattr(Client, "RECONCILE_BUDGET", 0.05)

        async def _hang(*_a, **_k):
            await asyncio.sleep(10)

        monkeypatch.setattr(client, "_probe_tcp", _hang)

        await client._reconcile_saved_server("srv-1", ["10.0.0.1"], 5555, "h")

        assert client_config.get_server_host() == "172.16.0.9"


class TestDiscoveryDoesNotBlock:
    @pytest.mark.anyio
    async def test_discover_returns_before_probes_finish(
        self, client, client_config, monkeypatch
    ):
        """Probing inline would nearly triple the discovery period.

        discover_servers already spends ~5s inside discover_services, and the
        loop re-ticks every 5s while disconnected.
        """
        client_config.set_server_connection(
            uid="srv-1", host="172.16.0.9", port=5555, hostname="server-host"
        )
        release = asyncio.Event()

        async def _blocked(*_a, **_k):
            await release.wait()
            return True

        monkeypatch.setattr(client, "_probe_tcp", _blocked)
        monkeypatch.setattr(
            "service.client.ServiceDiscovery",
            lambda *a, **k: _StubDiscovery([_svc(addresses=("10.0.0.1",))]),
        )

        await asyncio.wait_for(client.discover_servers(), timeout=1.0)

        assert client._reconcile_task is not None
        assert not client._reconcile_task.done()
        release.set()
        await client._reconcile_task

    @pytest.mark.anyio
    async def test_reentrancy_guard_keeps_one_reconcile_in_flight(
        self, client, client_config, monkeypatch
    ):
        client_config.set_server_connection(
            uid="srv-1", host="172.16.0.9", port=5555, hostname="server-host"
        )
        release = asyncio.Event()

        async def _blocked(*_a, **_k):
            await release.wait()
            return True

        monkeypatch.setattr(client, "_probe_tcp", _blocked)
        monkeypatch.setattr(
            "service.client.ServiceDiscovery",
            lambda *a, **k: _StubDiscovery([_svc(addresses=("10.0.0.1",))]),
        )

        await client.discover_servers()
        first = client._reconcile_task
        await client.discover_servers()

        assert client._reconcile_task is first
        release.set()
        await first


class TestChooseServerFirstConnection:
    """Level 1: a user who never opens Options must still connect."""

    @pytest.mark.anyio
    async def test_picks_the_reachable_candidate(
        self, client, client_config, reachable
    ):
        client._found_services = [_svc(addresses=("10.0.0.1", "192.168.1.20"))]
        reachable.set_live("192.168.1.20")

        assert await client.choose_server("srv-1") is True
        assert client_config.get_server_host() == "192.168.1.20"

    @pytest.mark.anyio
    async def test_falls_back_to_preferred_when_none_answer(
        self, client, client_config, reachable
    ):
        """A server still booting is the common case: keep something to retry."""
        client._found_services = [_svc(addresses=("10.0.0.1", "192.168.1.20"))]
        reachable.set_live()

        assert await client.choose_server("srv-1") is True
        assert client_config.get_server_host() == "10.0.0.1"

    @pytest.mark.anyio
    async def test_unknown_uid_reports_failure(self, client, reachable):
        """The daemon used to report success even when nothing was written."""
        client._found_services = [_svc(uid="srv-1")]

        assert await client.choose_server("does-not-exist") is False


class TestCertificateWipeSafety:
    """Dropping the CA forces a full OTP re-pair; it needs more than a UID delta."""

    def test_same_hostname_is_not_a_different_machine(self):
        """Isolated: the address deliberately does NOT match, so only the
        hostname branch can produce the answer."""
        svc = _svc(uid="srv-2", addresses=("192.168.9.9",), hostname="server-host")

        assert (
            Client._should_forget_previous_server("10.0.0.1", "server-host", svc)
            is False
        )

    def test_same_address_is_not_a_different_machine(self):
        """Isolated the other way: hostnames differ, so only the address
        branch can produce the answer."""
        svc = _svc(uid="srv-2", addresses=("10.0.0.1", "192.168.1.20"), hostname="new")

        assert (
            Client._should_forget_previous_server("10.0.0.1", "old-host", svc) is False
        )

    def test_address_match_on_a_non_preferred_entry_still_counts(self):
        """A multi-homed server publishes several; ours need not be first."""
        svc = _svc(uid="srv-2", addresses=("192.168.1.20", "10.0.0.1"), hostname="new")

        assert (
            Client._should_forget_previous_server("10.0.0.1", "old-host", svc) is False
        )

    def test_unknown_hostname_does_not_block_a_real_switch(self):
        """A client that never learned a hostname must still be able to switch."""
        svc = _svc(uid="srv-2", addresses=("192.168.9.9",), hostname="other-host")

        assert Client._should_forget_previous_server("10.0.0.1", None, svc) is True

    def test_nothing_in_common_is_a_real_switch(self):
        svc = _svc(uid="srv-2", addresses=("192.168.9.9",), hostname="other-host")

        assert (
            Client._should_forget_previous_server("10.0.0.1", "old-host", svc) is True
        )

    @pytest.mark.anyio
    async def test_automatic_pick_never_wipes(
        self, client, client_config, reachable, monkeypatch
    ):
        """The auto-select fires whenever the saved server is merely unreachable."""
        client_config.set_server_connection(
            uid="old-uid", host="10.0.0.1", port=5555, hostname="old-host"
        )
        client._found_services = [
            _svc(uid="new-uid", addresses=("192.168.9.9",), hostname="other")
        ]
        reachable.set_live("192.168.9.9")
        forgotten = []
        monkeypatch.setattr(
            client,
            "_forget_previous_server",
            lambda *a: forgotten.append(a),
        )

        await client.choose_server("new-uid")  # user_initiated defaults to False

        assert forgotten == []

    @pytest.mark.anyio
    async def test_user_initiated_switch_to_another_machine_wipes(
        self, client, client_config, reachable, monkeypatch
    ):
        client_config.set_server_connection(
            uid="old-uid", host="10.0.0.1", port=5555, hostname="old-host"
        )
        client._found_services = [
            _svc(uid="new-uid", addresses=("192.168.9.9",), hostname="other")
        ]
        reachable.set_live("192.168.9.9")
        forgotten = []
        monkeypatch.setattr(
            client,
            "_forget_previous_server",
            lambda *a: forgotten.append(a),
        )

        await client.choose_server("new-uid", user_initiated=True)

        assert len(forgotten) == 1


class _StubDiscovery:
    def __init__(self, services):
        self._services = services

    async def discover_services(self):
        return list(self._services)
