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

"""App id and restore tokens: what lets a portal grant outlive the daemon."""

import json
import os
import stat
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from config import ApplicationConfig
from input.mouse.backend import _portal

REPO = Path(__file__).resolve().parents[3]


def test_app_id_is_the_tauri_identifier():
    # Grants are keyed by it and it must name the shipped .desktop file.
    conf = json.loads((REPO / "src-gui/src-tauri/tauri.conf.json").read_text())
    assert ApplicationConfig.app_id == conf["identifier"]


class TestTokenStore:
    @pytest.fixture
    def store(self, tmp_path):
        return _portal.TokenStore(str(tmp_path / "state" / "tokens.json"))

    def test_round_trip(self, store):
        store.save("remote_desktop", "tok", "com.example.App")
        assert store.load("remote_desktop", "com.example.App") == "tok"

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes")
    def test_the_file_is_private(self, store):
        store.save("remote_desktop", "tok", "com.example.App")
        assert stat.S_IMODE(os.stat(store.path).st_mode) == 0o600

    def test_a_token_for_another_app_id_is_still_presented(self, store):
        # Presenting it costs at most the dialog the portal would show anyway.
        store.save("remote_desktop", "tok", "com.example.Old")
        assert store.load("remote_desktop", "com.example.App") == "tok"

    def test_clear_leaves_other_portals_alone(self, store):
        store.save("remote_desktop", "a", None)
        store.save("input_capture", "b", None)
        store.clear("remote_desktop")
        assert store.load("remote_desktop", None) is None
        assert store.load("input_capture", None) == "b"

    @pytest.mark.parametrize("content", ["not json", "[1, 2]", '{"remote_desktop": 3}'])
    def test_a_damaged_file_means_no_token(self, store, content):
        os.makedirs(os.path.dirname(store.path))
        Path(store.path).write_text(content)
        assert store.load("remote_desktop", None) is None

    def test_missing_file_means_no_token(self, store):
        assert store.load("remote_desktop", None) is None
        store.clear("remote_desktop")
        assert not os.path.exists(store.path)


class TestRegistration:
    @pytest.fixture(autouse=True)
    def fresh(self, monkeypatch):
        monkeypatch.setattr(_portal, "_register_tried", False)
        monkeypatch.setattr(_portal, "_registered", None)

    def _module(self, monkeypatch, register):
        mod = types.ModuleType("pyinputcapture")
        mod.register_host_app = register
        monkeypatch.setitem(sys.modules, "pyinputcapture", mod)
        return mod

    def test_registers_once_per_process(self, monkeypatch):
        register = MagicMock()
        self._module(monkeypatch, register)
        assert _portal.ensure_host_app_registered() == ApplicationConfig.app_id
        assert _portal.ensure_host_app_registered() == ApplicationConfig.app_id
        register.assert_called_once_with(ApplicationConfig.app_id)

    def test_a_portal_without_registry_is_not_asked_again(self, monkeypatch):
        # Too late to register after the first portal call has gone out.
        register = MagicMock(side_effect=RuntimeError("Registry.Register: no"))
        self._module(monkeypatch, register)
        assert _portal.ensure_host_app_registered() is None
        assert _portal.ensure_host_app_registered() is None
        register.assert_called_once()
