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

"""A broken input backend must degrade, never stop the daemon from starting.

The daemon owns the IPC socket: if it dies at import the GUI has nothing to
connect to. On Linux a missing bundled libei or dumpkeys does exactly that.
"""

import sys

import pytest

import utils
from utils import (
    backend_failures,
    backend_module,
    record_backend_failure,
    unavailable_symbol,
)


@pytest.fixture(autouse=True)
def _isolate_registry():
    """The failure registry is process-global; keep tests independent."""
    saved = list(utils.BACKEND_FAILURES)
    utils.BACKEND_FAILURES.clear()
    yield
    utils.BACKEND_FAILURES[:] = saved


def _install(monkeypatch, name, module):
    monkeypatch.setitem(sys.modules, name, module)


class TestUnavailableSymbol:
    def test_raises_on_call(self):
        stub = unavailable_symbol("Key", "libei.so.1 missing")
        with pytest.raises(RuntimeError, match="libei.so.1 missing"):
            stub()

    def test_raises_on_attribute_access(self):
        # They stand in for enums, so ``Key.esc`` must raise too.
        stub = unavailable_symbol("Key", "libei.so.1 missing")
        with pytest.raises(RuntimeError, match="libei.so.1 missing"):
            stub.esc


class TestBackendModuleDegrades:
    def test_returns_placeholder_module_instead_of_raising(self, monkeypatch):
        """Both the platform module and ``_base`` gone -> no exception."""

        def explode(name, package=None):
            raise ImportError("the binary dumpkeys is not installed")

        monkeypatch.setattr(utils.importlib, "import_module", explode)

        module = backend_module("input.keyboard")
        assert backend_failures(), "the failure must be recorded"
        # Attribute access returns a placeholder; the error comes at use.
        listener = module.ServerKeyboardListener
        with pytest.raises(RuntimeError, match="dumpkeys"):
            listener()

    def test_non_import_errors_also_degrade(self, monkeypatch):
        """``input/keyboard/_base.py`` builds tables from ``Key.*`` at module
        level, so with a degraded ``Key`` its import raises RuntimeError."""

        def explode(name, package=None):
            if name == "._base":
                raise RuntimeError("Key.ctrl is unavailable")
            raise ImportError("no backend")

        monkeypatch.setattr(utils.importlib, "import_module", explode)

        module = backend_module("input.keyboard")
        assert backend_failures(), "the RuntimeError must be recorded, not raised"
        with pytest.raises(RuntimeError):
            module.ServerKeyboardListener()

    def test_oserror_from_a_dlopened_library_degrades(self, monkeypatch):
        """snegg loads libei with ``ctypes.CDLL``: OSError, not ImportError."""

        def explode(name, package=None):
            raise OSError("libei.so.1: cannot open shared object file")

        monkeypatch.setattr(utils.importlib, "import_module", explode)

        module = backend_module("input.mouse")
        assert backend_failures()
        with pytest.raises(RuntimeError, match="libei"):
            module.ServerMouseListener()

    def test_unexpected_exception_is_not_swallowed(self, monkeypatch):
        """The degraded start is for packaging defects, not for real bugs."""

        def explode(name, package=None):
            raise ValueError("a real bug in the backend")

        monkeypatch.setattr(utils.importlib, "import_module", explode)

        with pytest.raises(ValueError, match="a real bug"):
            backend_module("input.mouse")

    def test_falls_back_to_base_when_available(self, monkeypatch):
        sentinel = object()

        def imp(name, package=None):
            if name == "._base":
                return sentinel
            raise ImportError("no platform module")

        monkeypatch.setattr(utils.importlib, "import_module", imp)

        assert backend_module("input.keyboard") is sentinel
        assert not backend_failures(), "a successful fallback is not a failure"


class TestResolveBackendDegrades:
    def test_failing_rule_is_skipped_and_recorded(self):
        from input._platform import BackendRule, resolve_backend

        rules = [
            BackendRule(
                condition=None,
                module="._missing_backend",
                symbols={"MouseListener": "MouseListener"},
                names={"mouse_listener": "libei"},
            )
        ]
        backend, exports = resolve_backend("input.mouse.backend", rules)

        assert backend["mouse_listener"] == "unavailable"
        assert backend_failures()
        with pytest.raises(RuntimeError):
            exports["MouseListener"]()

    def test_later_rule_still_provides_the_symbol(self):
        from input._platform import BackendRule, resolve_backend

        rules = [
            BackendRule(
                condition=None,
                module="._missing_backend",
                symbols={"Button": "Button"},
                names={"mouse_controller": "libei"},
            ),
            BackendRule(
                condition=None,
                module="enum",
                symbols={"Button": "IntEnum"},
                names={"mouse_controller": "fallback"},
            ),
        ]
        backend, exports = resolve_backend("input.mouse.backend", rules)

        from enum import IntEnum

        assert exports["Button"] is IntEnum
        assert backend["mouse_controller"] == "fallback"


class TestRegistry:
    def test_backend_failures_returns_a_copy(self):
        record_backend_failure("input.mouse", "._libei", "OSError: libei.so.1")
        snapshot = backend_failures()
        snapshot.clear()
        assert backend_failures(), "callers must not be able to clear the registry"
