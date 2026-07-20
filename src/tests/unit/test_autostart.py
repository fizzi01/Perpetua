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

"""Unit tests for autostart mode helpers and the Linux XDG backend.

The mode helpers are pure and run everywhere. The Linux backend only touches
``$XDG_CONFIG_HOME/autostart/*.desktop`` files, so it round-trips on any host
(no launchctl / winreg involved) — we point ``XDG_CONFIG_HOME`` at a tmp dir.
"""

import pytest

from utils.autostart import (
    MODE_CLIENT,
    MODE_OFF,
    MODE_PLAIN,
    MODE_SERVER,
    args_for_mode,
    mode_from_args,
)
from utils.autostart._base import AutostartStatus
from utils.autostart._darwin import _OPEN_PATH, _MacAutostartManager
from utils.autostart._linux import _LinuxAutostartManager


@pytest.mark.parametrize(
    "mode,expected_tail",
    [
        (MODE_SERVER, "--server"),
        (MODE_CLIENT, "--client"),
        (MODE_PLAIN, None),
        (MODE_OFF, None),
    ],
)
def test_args_for_mode(mode, expected_tail):
    args = args_for_mode(mode)
    assert args[0] == "--start-minimized"
    if expected_tail is None:
        assert args == ["--start-minimized"]
    else:
        assert args[-1] == expected_tail


@pytest.mark.parametrize(
    "args,expected",
    [
        (["--start-minimized", "--server"], MODE_SERVER),
        (["--start-minimized", "--client"], MODE_CLIENT),
        (["--start-minimized"], MODE_PLAIN),
        ([], MODE_PLAIN),
        (None, MODE_PLAIN),
    ],
)
def test_mode_from_args(args, expected):
    assert mode_from_args(args) == expected


def test_status_mode_property():
    assert AutostartStatus(enabled=False).mode == MODE_OFF
    assert (
        AutostartStatus(enabled=True, args=["--start-minimized", "--server"]).mode
        == MODE_SERVER
    )
    assert AutostartStatus(enabled=True, args=["--start-minimized"]).mode == MODE_PLAIN


@pytest.mark.parametrize("mode", [MODE_SERVER, MODE_CLIENT, MODE_PLAIN])
def test_linux_backend_roundtrip(tmp_path, monkeypatch, mode):
    """enable() then is_enabled() must recover the same exec path and mode."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    mgr = _LinuxAutostartManager()

    assert mgr.is_enabled().enabled is False

    exec_path = "/opt/Perpetua/Perpetua"
    mgr.enable(exec_path, args=args_for_mode(mode))

    status = mgr.is_enabled()
    assert status.enabled is True
    assert status.exec_path == exec_path
    assert status.mode == mode

    mgr.disable()
    assert mgr.is_enabled().enabled is False


def test_linux_backend_requires_absolute_path(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    mgr = _LinuxAutostartManager()
    with pytest.raises(ValueError):
        mgr.enable("Perpetua", args=args_for_mode(MODE_SERVER))


# --- macOS backend --------------------------------------------------------
#
# The plist round-trip is platform-agnostic (plistlib). We only stub out the
# ``launchctl`` bootstrap/bootout calls so the tests never touch the real
# per-user launchd domain, and redirect the plist to a tmp dir.


@pytest.fixture
def mac_manager(tmp_path, monkeypatch):
    monkeypatch.setattr(
        _MacAutostartManager,
        "_plist_path",
        property(lambda self: tmp_path / "com.federicoizzi.Perpetua.plist"),
    )
    monkeypatch.setattr(
        _MacAutostartManager, "_launchctl_bootstrap", staticmethod(lambda p: None)
    )
    monkeypatch.setattr(
        _MacAutostartManager, "_launchctl_bootout", staticmethod(lambda p: None)
    )
    return _MacAutostartManager()


@pytest.mark.parametrize("mode", [MODE_SERVER, MODE_CLIENT, MODE_PLAIN])
def test_mac_backend_bundle_roundtrip(mac_manager, mode):
    """A ``.app`` bundle must register via /usr/bin/open and round-trip."""
    import plistlib

    assert mac_manager.is_enabled().enabled is False

    bundle = "/Applications/Perpetua.app"
    mac_manager.enable(bundle, args=args_for_mode(mode))

    # The on-disk plist launches the bundle through LaunchServices, not the
    # inner Mach-O — this is what prevents the duplicate dock icon.
    with mac_manager._plist_path.open("rb") as f:
        argv = plistlib.load(f)["ProgramArguments"]
    assert argv[0] == _OPEN_PATH
    assert argv[1] == bundle
    assert "--args" in argv

    status = mac_manager.is_enabled()
    assert status.enabled is True
    assert status.exec_path == bundle  # bundle, not /usr/bin/open
    assert status.mode == mode

    mac_manager.disable()
    assert mac_manager.is_enabled().enabled is False


def test_mac_backend_direct_binary_legacy_form(mac_manager):
    """A non-.app path keeps the direct-launch form for back-compat."""
    import plistlib

    exec_path = "/opt/Perpetua/Perpetua"
    mac_manager.enable(exec_path, args=args_for_mode(MODE_SERVER))

    with mac_manager._plist_path.open("rb") as f:
        argv = plistlib.load(f)["ProgramArguments"]
    assert argv[0] == exec_path

    status = mac_manager.is_enabled()
    assert status.exec_path == exec_path
    assert status.mode == MODE_SERVER


def test_mac_backend_requires_absolute_path(mac_manager):
    with pytest.raises(ValueError):
        mac_manager.enable("Perpetua.app", args=args_for_mode(MODE_SERVER))


@pytest.mark.parametrize(
    "argv,expected_exec,expected_args",
    [
        (
            [
                "/usr/bin/open",
                "/Applications/Perpetua.app",
                "--args",
                "--start-minimized",
                "--server",
            ],
            "/Applications/Perpetua.app",
            ["--start-minimized", "--server"],
        ),
        (
            ["/usr/bin/open", "/Applications/Perpetua.app", "--args"],
            "/Applications/Perpetua.app",
            [],
        ),
        (
            ["/opt/Perpetua/Perpetua", "--start-minimized", "--client"],
            "/opt/Perpetua/Perpetua",
            ["--start-minimized", "--client"],
        ),
    ],
)
def test_mac_parse_program_arguments(argv, expected_exec, expected_args):
    exec_path, args = _MacAutostartManager._parse_program_arguments(argv, {})
    assert exec_path == expected_exec
    assert args == expected_args
