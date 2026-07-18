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

"""Regression tests for the legacy ``~/.perpetua`` -> XDG config migration.

The migration lives in ``ApplicationConfig._migrate_legacy_linux_layout`` and
is platform-independent logic (it operates on the paths it's handed), so these
tests exercise it directly regardless of the host OS. The headline regression:
the guard must NOT instantiate ``ApplicationConfig()`` (which would create the
new config as a side effect and silently defeat the migration).
"""

import os
import sys

import pytest

import config as config_module
from config import ApplicationConfig

# The ``~/.perpetua`` -> XDG migration is a Linux-only concern: only
# ``get_main_path`` on Linux ever calls ``_migrate_legacy_linux_layout``.
# macOS/Windows keep their historical per-OS layout (Library/Caches,
# %LOCALAPPDATA%) and never migrate, so this suite runs on Linux only.
pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="legacy ~/.perpetua -> XDG migration is Linux-only",
)


@pytest.fixture
def reset_migration_flag():
    """The migration is one-shot per process; reset the flag around each test."""
    config_module._legacy_linux_migration_done = False
    yield
    config_module._legacy_linux_migration_done = False


def _write(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def test_legacy_config_is_migrated(tmp_path, monkeypatch, reset_migration_flag):
    """A real ``~/.perpetua/config/config.json`` must land in the XDG dir intact."""
    home = tmp_path
    # The migration resolves the legacy dir via ``~``; point HOME at the tmp dir.
    monkeypatch.setenv("HOME", str(home))
    legacy = home / ".perpetua"
    marker = '{"general": {"_marker": "legacy-user-config"}}'
    _write(str(legacy / "config" / "config.json"), marker)
    _write(str(legacy / "daemon.log"), "old log line\n")

    new_config_dir = home / ".config" / "perpetua"
    state_dir = home / ".local" / "state" / "perpetua"

    ApplicationConfig._migrate_legacy_linux_layout(str(new_config_dir), str(state_dir))

    migrated = new_config_dir / "config" / "config.json"
    assert migrated.is_file(), "config was not migrated to the XDG dir"
    # The crux: it's the USER's config, not a freshly-created default.
    assert migrated.read_text(encoding="utf-8") == marker
    # Log moved to the state dir.
    assert (state_dir / "daemon.log").is_file()


def test_migration_skips_when_new_config_exists(
    tmp_path, monkeypatch, reset_migration_flag
):
    """If the XDG config already exists, the legacy tree is left untouched."""
    home = tmp_path
    monkeypatch.setenv("HOME", str(home))
    legacy = home / ".perpetua"
    _write(str(legacy / "config" / "config.json"), "legacy")

    new_config_dir = home / ".config" / "perpetua"
    _write(str(new_config_dir / "config" / "config.json"), "already-here")

    ApplicationConfig._migrate_legacy_linux_layout(str(new_config_dir), str(home))

    # New config untouched; legacy still present for manual recovery.
    assert (new_config_dir / "config" / "config.json").read_text() == "already-here"
    assert (legacy / "config" / "config.json").is_file()


def test_migration_is_noop_without_legacy(tmp_path, monkeypatch, reset_migration_flag):
    """No legacy dir -> nothing created, no crash."""
    monkeypatch.setenv("HOME", str(tmp_path))
    new_config_dir = tmp_path / ".config" / "perpetua"

    ApplicationConfig._migrate_legacy_linux_layout(str(new_config_dir), str(tmp_path))

    assert not new_config_dir.exists()
