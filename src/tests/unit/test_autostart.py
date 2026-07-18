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
