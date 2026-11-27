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

"""Wayland backends follow what the portal offers, not the desktop's name."""

import sys
import types
from unittest.mock import MagicMock

import pytest

from input import _platform, _portal


@pytest.fixture
def wayland(monkeypatch):
    """A Linux Wayland session whose portal answers with ``probe``."""
    _platform.portal_versions.cache_clear()
    monkeypatch.setattr(_platform, "is_linux", lambda: True)
    monkeypatch.setattr(_platform, "is_wayland", lambda: True)
    calls = []
    monkeypatch.setattr(
        _portal, "ensure_host_app_registered", lambda: calls.append("register")
    )
    mod = types.ModuleType("pyinputcapture")

    def probe(timeout):
        calls.append("probe")
        return mod.answer() if callable(mod.answer) else mod.answer

    mod.portal_versions = probe
    mod.answer = {"remote_desktop": 2, "input_capture": 1}
    mod.calls = calls
    monkeypatch.setitem(sys.modules, "pyinputcapture", mod)
    yield mod
    _platform.portal_versions.cache_clear()


class TestProbe:
    def test_the_app_id_is_registered_before_the_portal_is_asked(self, wayland):
        _platform.portal_versions()
        assert wayland.calls == ["register", "probe"]

    def test_the_portal_is_asked_once(self, wayland):
        _platform.remote_desktop_available()
        _platform.input_capture_available()
        assert wayland.calls.count("probe") == 1

    @pytest.mark.parametrize(
        "answer, emulate, capture",
        [
            ({"remote_desktop": 2, "input_capture": 1}, True, True),
            ({"remote_desktop": 1, "input_capture": 1}, False, True),
            ({"remote_desktop": 2, "input_capture": 0}, True, False),
            ({"remote_desktop": 0, "input_capture": 0}, False, False),
        ],
        ids=["both", "rd-v1-has-no-eis", "no-capture", "none"],
    )
    def test_each_side_follows_its_own_portal(self, wayland, answer, emulate, capture):
        wayland.answer = answer
        assert _platform.remote_desktop_available() is emulate
        assert _platform.input_capture_available() is capture
        # Only a libei sender is blind to the cursor.
        assert _platform.cursor_feedback_available() is (not emulate)

    def test_a_portal_that_cannot_be_asked_offers_nothing(self, wayland):
        def boom():
            raise TimeoutError("the portal did not answer")

        wayland.answer = boom
        assert _platform.portal_versions() == {}
        assert not _platform.remote_desktop_available()

    def test_an_old_pyinputcapture_offers_nothing(self, wayland):
        del wayland.portal_versions
        assert _platform.portal_versions() == {}

    def test_off_wayland_the_portal_is_never_asked(self, wayland, monkeypatch):
        monkeypatch.setattr(_platform, "is_wayland", lambda: False)
        assert _platform.portal_versions() == {}
        assert wayland.calls == []
        assert _platform.cursor_feedback_available()


class TestMouseBackendRules:
    """Listener and controller are chosen independently."""

    def _resolve(self, monkeypatch, emulate, capture):
        from input.mouse import backend

        monkeypatch.setattr(backend, "is_linux", lambda: True)
        monkeypatch.setattr(backend, "is_wayland", lambda: True)
        monkeypatch.setattr(backend, "remote_desktop_available", lambda: emulate)
        monkeypatch.setattr(backend, "input_capture_available", lambda: capture)
        libei = MagicMock(MouseListener="libei-L", MouseController="libei-C")
        dummy = MagicMock(MouseListener="dummy-L", MouseController="dummy-C")
        # The unconditional pynput fallback is imported too; it must lose.
        modules = {"._libei": libei, "._dummy": dummy, "pynput.mouse": MagicMock()}
        monkeypatch.setattr(
            _platform, "import_module", lambda name, pkg=None: modules[name]
        )
        names, symbols = _platform.resolve_backend(backend.__name__, backend._RULES)
        return names, symbols["MouseListener"], symbols["MouseController"]

    @pytest.mark.parametrize(
        "emulate, capture, listener, controller",
        [
            (True, True, "libei-L", "libei-C"),
            (True, False, "dummy-L", "libei-C"),
            (False, True, "libei-L", "dummy-C"),
            (False, False, "dummy-L", "dummy-C"),
        ],
    )
    def test_each_side_gets_libei_when_its_portal_is_there(
        self, monkeypatch, emulate, capture, listener, controller
    ):
        names, got_listener, got_controller = self._resolve(
            monkeypatch, emulate, capture
        )
        assert (got_listener, got_controller) == (listener, controller)
        assert names["mouse_listener"] == listener.split("-")[0]
        assert names["mouse_controller"] == controller.split("-")[0]
