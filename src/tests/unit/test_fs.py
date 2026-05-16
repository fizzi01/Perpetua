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

import os
import stat
import sys

import pytest

from utils.fs import atomic_write_bytes, atomic_write_text


def test_atomic_write_bytes_round_trip(tmp_path):
    target = tmp_path / "blob.bin"
    payload = b"\x00\x01\x02hello\xff"

    atomic_write_bytes(target, payload)

    assert target.read_bytes() == payload


def test_atomic_write_text_round_trip(tmp_path):
    target = tmp_path / "note.txt"
    text = "ciao — utf-8 €"

    atomic_write_text(target, text)

    assert target.read_text(encoding="utf-8") == text


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only chmod semantics")
def test_atomic_write_bytes_applies_mode(tmp_path):
    target = tmp_path / "secret.key"

    atomic_write_bytes(target, b"private", mode=0o600)

    perms = stat.S_IMODE(os.stat(target).st_mode)
    assert perms == 0o600, f"expected 0o600, got {oct(perms)}"


def test_atomic_write_bytes_unlinks_temp_on_failure(tmp_path, monkeypatch):
    target = tmp_path / "willfail.bin"
    # Pre-populate the destination so we can verify it's unchanged.
    target.write_bytes(b"OLD")

    def boom(_fd, _data):
        raise RuntimeError("simulated write failure")

    monkeypatch.setattr(os, "write", boom)

    with pytest.raises(RuntimeError, match="simulated write failure"):
        atomic_write_bytes(target, b"NEW")

    # Destination preserved.
    assert target.read_bytes() == b"OLD"
    # No temp leftovers in the directory.
    leftovers = [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == [], f"temp files leaked: {leftovers}"


def test_atomic_write_bytes_unlinks_temp_when_replace_fails(tmp_path, monkeypatch):
    target = tmp_path / "willfail.bin"
    target.write_bytes(b"OLD")

    def boom(_src, _dst):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(os, "replace", boom)

    with pytest.raises(OSError, match="simulated replace failure"):
        atomic_write_bytes(target, b"NEW")

    assert target.read_bytes() == b"OLD"
    leftovers = [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == [], f"temp files leaked: {leftovers}"


def test_atomic_write_temp_on_same_filesystem(tmp_path, monkeypatch):
    """Temp file must live in the same directory as the target (atomic rename)."""
    target = tmp_path / "blob.bin"
    captured = {}

    real_mkstemp = __import__("tempfile").mkstemp

    def tracking_mkstemp(*args, **kwargs):
        captured["dir"] = kwargs.get("dir")
        return real_mkstemp(*args, **kwargs)

    monkeypatch.setattr("utils.fs.tempfile.mkstemp", tracking_mkstemp)

    atomic_write_bytes(target, b"data")

    assert captured["dir"] == str(tmp_path)


def test_atomic_write_bytes_overwrites_existing(tmp_path):
    target = tmp_path / "existing.bin"
    target.write_bytes(b"OLD")

    atomic_write_bytes(target, b"NEW")

    assert target.read_bytes() == b"NEW"
