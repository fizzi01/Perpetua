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

"""Unit tests for the ``utils.screen`` façade.

Two layers are covered:

* The platform-agnostic :class:`Screen` base logic (size formatting,
  virtual-bbox derivation, monitor caching). These use a stub subclass
  with a fake ``get_size`` so they run everywhere - including the
  headless CI runners on GitHub Actions.
* The real per-OS ``Screen`` backend that queries the actual displays.
  These need a live windowing system, so they are **skipped in a
  CLI-only / headless environment** via ``requires_display``.
"""

import os
import sys

import pytest

from utils.screen import MonitorInfo, MonitorLayout, Screen
from utils.screen import _base


# ---------------------------------------------------------------------------
# Headless detection: real-display tests are suppressed where there is no GUI
# (the GitHub Actions runners are headless, but they still import this module).
# ---------------------------------------------------------------------------
def _display_available() -> bool:
    if sys.platform == "darwin":
        # Quartz is always linkable on macOS; a size query is the honest probe.
        try:
            w, h = Screen.get_size()
        except Exception:
            return False
        return w > 0 and h > 0
    if sys.platform.startswith("linux"):
        return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    if sys.platform in ("win32", "cygwin"):
        return True
    return False


requires_display = pytest.mark.skipif(
    not _display_available(),
    reason="No display available (CLI-only / headless environment).",
)


class _StubScreen(_base.Screen):
    """A base ``Screen`` with a deterministic primary size and no OS calls."""

    _size = (1920, 1080)

    @classmethod
    def get_size(cls) -> tuple[int, int]:
        return cls._size


@pytest.fixture(autouse=True)
def _clean_monitors_cache():
    """The monitor cache is a module-level global; keep tests isolated."""
    _base.invalidate_monitors_cache()
    yield
    _base.invalidate_monitors_cache()


# ===========================================================================
# Base Screen facade - pure logic, runs in every environment.
# ===========================================================================
class TestScreenSizeHelpers:
    def test_base_get_size_is_not_implemented(self):
        with pytest.raises(NotImplementedError):
            _base.Screen.get_size()

    def test_get_size_str_formats_as_wxh(self):
        assert _StubScreen.get_size_str() == "1920x1080"

    def test_get_size_str_rounds_float_dimensions(self):
        class _FloatScreen(_StubScreen):
            _size = (1920.4, 1080.6)

        # Uses ``{:.0f}`` formatting, so it renders integers.
        assert _FloatScreen.get_size_str() == "1920x1081"


class TestVirtualBbox:
    def test_default_bbox_degrades_to_primary(self):
        assert _StubScreen.get_virtual_bbox() == (0, 0, 1920, 1080)

    def test_virtual_size_is_bbox_span(self):
        assert _StubScreen.get_virtual_size() == (1920, 1080)

    def test_virtual_size_uses_max_minus_min(self):
        class _OffsetScreen(_base.Screen):
            @classmethod
            def get_virtual_bbox(cls):
                return -100, -50, 1820, 1030

        assert _OffsetScreen.get_virtual_size() == (1920, 1080)


class TestBaseGetMonitors:
    def test_default_returns_single_primary_monitor(self):
        monitors = _StubScreen.get_monitors()
        assert len(monitors) == 1
        m = monitors[0]
        assert isinstance(m, MonitorInfo)
        assert (m.min_x, m.min_y, m.max_x, m.max_y) == (0, 0, 1920, 1080)
        assert m.is_primary is True
        assert m.monitor_id == 0

    def test_get_monitor_layout_wraps_monitors(self):
        layout = _StubScreen.get_monitor_layout()
        assert isinstance(layout, MonitorLayout)
        assert len(layout.monitors) == 1
        assert layout.virtual_bbox == (0, 0, 1920, 1080)


class TestBaseUnimplementedMethods:
    def test_is_screen_locked_not_implemented(self):
        # Regression: these used to be mis-indented inside
        # ``invalidate_monitors_cache`` and were absent from the class.
        with pytest.raises(NotImplementedError):
            _base.Screen.is_screen_locked()

    def test_hide_icon_not_implemented(self):
        with pytest.raises(NotImplementedError):
            _base.Screen.hide_icon()


class TestMonitorsCache:
    def test_cached_result_avoids_second_query(self):
        calls = {"n": 0}

        class _CountingScreen(_StubScreen):
            @classmethod
            def get_monitors(cls):
                calls["n"] += 1
                return super().get_monitors()

        first = _CountingScreen.get_monitors_cached()
        second = _CountingScreen.get_monitors_cached()
        assert calls["n"] == 1
        # Returns an independent list copy each call, equal in content.
        assert first == second
        assert first is not second

    def test_invalidate_forces_requery(self):
        calls = {"n": 0}

        class _CountingScreen(_StubScreen):
            @classmethod
            def get_monitors(cls):
                calls["n"] += 1
                return super().get_monitors()

        _CountingScreen.get_monitors_cached()
        _base.invalidate_monitors_cache()
        _CountingScreen.get_monitors_cached()
        assert calls["n"] == 2

    def test_ttl_expiry_triggers_requery(self, monkeypatch):
        calls = {"n": 0}

        class _CountingScreen(_StubScreen):
            @classmethod
            def get_monitors(cls):
                calls["n"] += 1
                return super().get_monitors()

        clock = {"t": 1000.0}
        monkeypatch.setattr(_base.time, "monotonic", lambda: clock["t"])

        _CountingScreen.get_monitors_cached()
        assert calls["n"] == 1

        # Still inside the TTL window: served from cache.
        clock["t"] += _base._MONITORS_CACHE_TTL / 2
        _CountingScreen.get_monitors_cached()
        assert calls["n"] == 1

        # Past the TTL: re-queried.
        clock["t"] += _base._MONITORS_CACHE_TTL
        _CountingScreen.get_monitors_cached()
        assert calls["n"] == 2

    def test_cache_returns_defensive_copy(self):
        result = _StubScreen.get_monitors_cached()
        result.clear()
        # Mutating the returned list must not poison the cache.
        assert len(_StubScreen.get_monitors_cached()) == 1


# ===========================================================================
# Real per-OS backend - requires a live display, skipped when headless.
# ===========================================================================
@requires_display
class TestRealScreenBackend:
    def test_get_size_returns_positive_dimensions(self):
        w, h = Screen.get_size()
        assert w > 0 and h > 0

    def test_get_monitors_returns_at_least_one(self):
        monitors = Screen.get_monitors()
        assert len(monitors) >= 1
        assert all(isinstance(m, MonitorInfo) for m in monitors)
        # Every monitor has a non-degenerate area.
        for m in monitors:
            assert m.max_x > m.min_x
            assert m.max_y > m.min_y

    def test_exactly_one_primary_when_flagged(self):
        monitors = Screen.get_monitors()
        primaries = [m for m in monitors if m.is_primary]
        # Real backends flag a single primary; never more than one.
        assert len(primaries) <= 1

    def test_virtual_bbox_covers_every_monitor(self):
        min_x, min_y, max_x, max_y = Screen.get_virtual_bbox()
        for m in Screen.get_monitors():
            assert min_x <= m.min_x
            assert min_y <= m.min_y
            assert max_x >= m.max_x
            assert max_y >= m.max_y

    def test_get_monitor_layout_matches_get_monitors(self):
        layout = Screen.get_monitor_layout()
        assert isinstance(layout, MonitorLayout)
        assert len(layout.monitors) == len(Screen.get_monitors())
