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

import asyncio

import pytest

import utils.net
from utils import BackgroundTasks


def test_get_local_ip():
    ip = utils.net.get_local_ip()
    assert ip is not None, "Local IP address should not be None"
    octets = ip.split(".")
    assert len(octets) == 4, "IP address should have 4 octets"
    # It should not be a loopback address
    assert not ip.startswith("127."), (
        "Local IP address should not be a loopback address"
    )
    # It should not be a 0.0.0.0
    assert ip != "0.0.0.0", "Local IP address should not be 0.0.0.0"
    for octet in octets:
        assert 0 <= int(octet) <= 255, (
            f"Each octet should be between 0 and 255, got {octet}"
        )


def _unreachable(*_args, **_kwargs):
    raise OSError(51, "Network is unreachable")


@pytest.fixture
def net_probes(monkeypatch):
    """Control every get_local_ip() strategy independently.

    ``routes`` maps a probe target host to its answer: an IP string, or an
    exception instance/callable to raise. Anything missing is unreachable.
    """
    from utils.net import _base

    routes: dict = {}
    hostname_result: list = []

    def fake_probe(host, port):
        outcome = routes.get(host)
        if outcome is None:
            _unreachable()
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def fake_getaddrinfo(*_args, **_kwargs):
        if isinstance(hostname_result, BaseException):
            raise hostname_result
        return [(None, None, None, "", (ip, 0)) for ip in hostname_result]

    monkeypatch.setattr(_base.CommonNetInfo, "_probe_route", staticmethod(fake_probe))
    monkeypatch.setattr(_base.socket, "getaddrinfo", fake_getaddrinfo)
    utils.net.invalidate_local_ip_cache()

    class Control:
        def configure(self, unicast=None, multicast=None, hostname=()):
            nonlocal hostname_result
            routes.clear()
            if unicast is not None:
                routes[_base.CommonNetInfo.HOST] = unicast
            if multicast is not None:
                routes[_base.CommonNetInfo.MULTICAST_HOST] = multicast
            hostname_result = hostname
            utils.net.invalidate_local_ip_cache()

    yield Control()
    utils.net.invalidate_local_ip_cache()


def test_get_local_ip_uses_unicast_probe_first(net_probes):
    net_probes.configure(unicast="192.168.1.10", multicast="10.0.0.5")
    assert utils.net.get_local_ip() == "192.168.1.10"


def test_get_local_ip_falls_back_to_multicast_without_default_route(net_probes):
    """A LAN with no internet access has no default route: mDNS probe answers."""
    net_probes.configure(unicast=None, multicast="192.168.1.52")
    assert utils.net.get_local_ip() == "192.168.1.52"


def test_get_local_ip_falls_back_to_hostname(net_probes):
    net_probes.configure(hostname=["127.0.0.1", "192.168.1.77"])
    assert utils.net.get_local_ip() == "192.168.1.77"


def test_get_local_ip_rejects_unusable_addresses(net_probes):
    """Loopback / link-local answers are not advertisable, so keep looking."""
    net_probes.configure(
        unicast="127.0.0.1", multicast="169.254.3.4", hostname=["192.168.1.99"]
    )
    assert utils.net.get_local_ip() == "192.168.1.99"


def test_get_local_ip_exception(net_probes):
    """Every strategy failing still raises MissingIpError with the usual text."""
    net_probes.configure(hostname=OSError(51, "Network is unreachable"))
    with pytest.raises(utils.net.MissingIpError) as excinfo:
        utils.net.get_local_ip()
    assert "Could not determine local IP address" in str(excinfo.value)


def test_get_local_ip_no_usable_candidate(net_probes):
    """Only loopback available -> treated as no address at all."""
    net_probes.configure(unicast="127.0.0.1", hostname=["127.0.0.1"])
    with pytest.raises(utils.net.MissingIpError):
        utils.net.get_local_ip()


def test_probe_route_uses_datagram_socket(monkeypatch):
    """The probe must never open a TCP connection (it would need a live peer)."""
    import socket as socket_mod

    from utils.net import _base

    seen = {}
    original_socket = socket_mod.socket

    class RecordingSocket:
        def __init__(self, family, type_):
            seen["family"] = family
            seen["type"] = type_

        def settimeout(self, _timeout):
            pass

        def connect(self, address):
            seen["address"] = address

        def getsockname(self):
            return ("192.168.1.1", 0)

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    monkeypatch.setattr(_base.socket, "socket", RecordingSocket)
    try:
        assert _base.CommonNetInfo._probe_route("224.0.0.251", 5353) == "192.168.1.1"
    finally:
        monkeypatch.setattr(_base.socket, "socket", original_socket)

    assert seen["type"] == socket_mod.SOCK_DGRAM
    assert seen["address"] == ("224.0.0.251", 5353)


# ---------------------------------------------------------------------------
# BackgroundTasks
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_background_tasks_spawn_success_no_log(monkeypatch):
    """Successful tasks do not produce WARNING logs from BackgroundTasks."""
    import utils as utils_pkg

    calls = []
    monkeypatch.setattr(
        utils_pkg._bg_logger,
        "warning",
        lambda msg, **kw: calls.append((msg, kw)),
    )

    bg = BackgroundTasks()

    async def ok():
        return 42

    task = bg.spawn(ok())
    await task
    await asyncio.sleep(0)

    assert task.result() == 42
    assert len(bg) == 0, "task should be discarded after completion"
    assert calls == [], f"unexpected log calls: {calls}"


@pytest.mark.anyio
async def test_background_tasks_spawn_exception_logs_warning(monkeypatch):
    """A raising task triggers logger.warning via the discard callback."""
    import utils as utils_pkg

    calls = []
    monkeypatch.setattr(
        utils_pkg._bg_logger,
        "warning",
        lambda msg, **kw: calls.append(msg),
    )

    bg = BackgroundTasks()

    async def boom():
        raise RuntimeError("kaboom")

    task = bg.spawn(boom(), name="explosion")
    with pytest.raises(RuntimeError):
        await task
    await asyncio.sleep(0)

    assert len(bg) == 0
    assert len(calls) == 1, f"expected one warning, got: {calls}"
    msg = calls[0]
    assert "kaboom" in msg, f"missing exception message: {msg!r}"
    assert "explosion" in msg, f"missing task name: {msg!r}"
    assert "RuntimeError" in msg, f"missing exception type: {msg!r}"


@pytest.mark.anyio
async def test_background_tasks_drain_cancel():
    """drain(cancel=True) cancels in-flight tasks without raising."""
    bg = BackgroundTasks()

    async def long_running():
        await asyncio.sleep(10)

    bg.spawn(long_running())
    bg.spawn(long_running())
    assert len(bg) == 2

    await bg.drain(cancel=True)
    assert len(bg) == 0


@pytest.mark.anyio
async def test_background_tasks_cancelled_no_log(monkeypatch):
    """Cancelled tasks should NOT emit a warning (cancellation is expected)."""
    import utils as utils_pkg

    calls = []
    monkeypatch.setattr(
        utils_pkg._bg_logger,
        "warning",
        lambda msg, **kw: calls.append(msg),
    )

    bg = BackgroundTasks()

    async def sleeper():
        await asyncio.sleep(10)

    bg.spawn(sleeper(), name="canceltest")
    await bg.drain(cancel=True)
    await asyncio.sleep(0)

    assert calls == [], f"cancellation should not log: {calls}"
