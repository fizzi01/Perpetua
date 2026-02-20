"""
Unit tests for Linux-specific clipboard implementation.
Tests cover URI-list parsing, X11/Wayland subprocess backends,
fallback logic, and _try_get_clip_file resolution.
"""

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

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from input.clipboard._linux import Clipboard


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_proc(returncode: int, stdout: bytes) -> MagicMock:
    """Build a fake CompletedProcess-like mock."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdout = stdout
    return proc


def _uri_list(*paths: str) -> bytes:
    """Encode a sequence of absolute paths as a text/uri-list payload."""
    lines = [f"file://{p}" for p in paths]
    return "\n".join(lines).encode()


# ---------------------------------------------------------------------------
# _read_uri_list_x11
# ---------------------------------------------------------------------------


class TestReadUriListX11:
    def test_returns_content_on_success(self):
        proc = _make_proc(0, b"file:///tmp/a.txt\n")
        with patch("subprocess.run", return_value=proc) as mock_run:
            result = Clipboard._read_uri_list_x11()
        assert result == "file:///tmp/a.txt\n"
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "xclip"
        assert "text/uri-list" in cmd

    def test_returns_none_on_nonzero_returncode(self):
        proc = _make_proc(1, b"")
        with patch("subprocess.run", return_value=proc):
            result = Clipboard._read_uri_list_x11()
        assert result is None

    def test_returns_none_when_stdout_empty(self):
        proc = _make_proc(0, b"")
        with patch("subprocess.run", return_value=proc):
            result = Clipboard._read_uri_list_x11()
        assert result is None

    def test_returns_none_when_xclip_not_found(self):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = Clipboard._read_uri_list_x11()
        assert result is None

    def test_returns_none_on_timeout(self):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("xclip", 1)):
            result = Clipboard._read_uri_list_x11()
        assert result is None

    def test_returns_none_on_os_error(self):
        with patch("subprocess.run", side_effect=OSError):
            result = Clipboard._read_uri_list_x11()
        assert result is None


# ---------------------------------------------------------------------------
# _read_uri_list_wayland
# ---------------------------------------------------------------------------


class TestReadUriListWayland:
    def test_returns_content_on_success(self):
        proc = _make_proc(0, b"file:///home/user/doc.pdf\n")
        with patch("subprocess.run", return_value=proc) as mock_run:
            result = Clipboard._read_uri_list_wayland()
        assert result == "file:///home/user/doc.pdf\n"
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "wl-paste"
        assert "text/uri-list" in cmd

    def test_returns_none_on_nonzero_returncode(self):
        proc = _make_proc(1, b"")
        with patch("subprocess.run", return_value=proc):
            result = Clipboard._read_uri_list_wayland()
        assert result is None

    def test_returns_none_when_wl_paste_not_found(self):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = Clipboard._read_uri_list_wayland()
        assert result is None

    def test_returns_none_on_timeout(self):
        with patch(
            "subprocess.run", side_effect=subprocess.TimeoutExpired("wl-paste", 1)
        ):
            result = Clipboard._read_uri_list_wayland()
        assert result is None


# ---------------------------------------------------------------------------
# _get_clipboard_files  (URI parsing + backend fallback)
# ---------------------------------------------------------------------------


class TestGetClipboardFiles:
    def test_parses_single_file(self, tmp_path):
        f = tmp_path / "report.pdf"
        f.write_text("data")
        raw = _uri_list(str(f))

        with patch.object(Clipboard, "_read_uri_list_x11", return_value=raw.decode()):
            result = Clipboard._get_clipboard_files()

        assert result is not None
        assert (str(f), "file") in result

    def test_parses_directory(self, tmp_path):
        d = tmp_path / "my_folder"
        d.mkdir()
        raw = _uri_list(str(d))

        with patch.object(Clipboard, "_read_uri_list_x11", return_value=raw.decode()):
            result = Clipboard._get_clipboard_files()

        assert result is not None
        assert (str(d), "directory") in result

    def test_parses_multiple_entries(self, tmp_path):
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("a")
        f2.write_text("b")
        raw = _uri_list(str(f1), str(f2))

        with patch.object(Clipboard, "_read_uri_list_x11", return_value=raw.decode()):
            result = Clipboard._get_clipboard_files()

        paths = [r[0] for r in result]
        assert str(f1) in paths
        assert str(f2) in paths

    def test_skips_comment_lines(self, tmp_path):
        f = tmp_path / "real.txt"
        f.write_text("x")
        raw = b"# this is a comment\nfile://" + str(f).encode() + b"\n"

        with patch.object(Clipboard, "_read_uri_list_x11", return_value=raw.decode()):
            result = Clipboard._get_clipboard_files()

        assert result is not None
        assert len(result) == 1
        assert result[0][0] == str(f)

    def test_skips_non_file_uris(self, tmp_path):
        raw = b"https://example.com/file.txt\n"

        with patch.object(Clipboard, "_read_uri_list_x11", return_value=raw.decode()):
            result = Clipboard._get_clipboard_files()

        assert result is None

    def test_decodes_percent_encoded_path(self, tmp_path):
        f = tmp_path / "my file.txt"
        f.write_text("hello")
        encoded = f"file://{str(f).replace(' ', '%20')}"
        raw = encoded.encode() + b"\n"

        with patch.object(Clipboard, "_read_uri_list_x11", return_value=raw.decode()):
            result = Clipboard._get_clipboard_files()

        assert result is not None
        assert result[0][0] == str(f)

    def test_unknown_type_for_nonexistent_path(self):
        raw = b"file:///nonexistent/path/ghost.bin\n"

        with patch.object(Clipboard, "_read_uri_list_x11", return_value=raw.decode()):
            result = Clipboard._get_clipboard_files()

        assert result is not None
        assert result[0] == ("/nonexistent/path/ghost.bin", "unknown")

    def test_returns_none_when_no_file_uris(self):
        with (
            patch.object(Clipboard, "_read_uri_list_x11", return_value=None),
            patch.object(Clipboard, "_read_uri_list_wayland", return_value=None),
        ):
            result = Clipboard._get_clipboard_files()

        assert result is None

    def test_falls_back_to_wayland_when_x11_unavailable(self, tmp_path):
        f = tmp_path / "wayland_file.txt"
        f.write_text("wl")
        raw = _uri_list(str(f)).decode()

        with (
            patch.object(Clipboard, "_read_uri_list_x11", return_value=None),
            patch.object(Clipboard, "_read_uri_list_wayland", return_value=raw),
        ):
            result = Clipboard._get_clipboard_files()

        assert result is not None
        assert result[0][0] == str(f)

    def test_x11_takes_priority_over_wayland(self, tmp_path):
        f_x11 = tmp_path / "x11.txt"
        f_wl = tmp_path / "wayland.txt"
        f_x11.write_text("x")
        f_wl.write_text("w")

        with (
            patch.object(
                Clipboard,
                "_read_uri_list_x11",
                return_value=_uri_list(str(f_x11)).decode(),
            ),
            patch.object(
                Clipboard,
                "_read_uri_list_wayland",
                return_value=_uri_list(str(f_wl)).decode(),
            ),
        ):
            result = Clipboard._get_clipboard_files()

        # Only x11 result should be present
        paths = [r[0] for r in result]
        assert str(f_x11) in paths
        assert str(f_wl) not in paths


# ---------------------------------------------------------------------------
# _try_get_clip_file
# ---------------------------------------------------------------------------


class TestTryGetClipFile:
    def test_resolves_to_full_path_when_found(self, tmp_path):
        f = tmp_path / "document.txt"
        f.write_text("content")

        with patch.object(
            Clipboard, "_get_clipboard_files", return_value=[(str(f), "file")]
        ):
            result = Clipboard._try_get_clip_file("document.txt")

        assert result == str(f)

    def test_returns_original_when_no_match(self, tmp_path):
        f = tmp_path / "other.txt"
        f.write_text("x")

        with patch.object(
            Clipboard, "_get_clipboard_files", return_value=[(str(f), "file")]
        ):
            result = Clipboard._try_get_clip_file("document.txt")

        assert result == "document.txt"

    def test_returns_original_when_clipboard_empty(self):
        with patch.object(Clipboard, "_get_clipboard_files", return_value=None):
            result = Clipboard._try_get_clip_file("/some/path/file.txt")

        assert result == "/some/path/file.txt"

    def test_ignores_directory_entries(self, tmp_path):
        d = tmp_path / "mydir"
        d.mkdir()

        with patch.object(
            Clipboard, "_get_clipboard_files", return_value=[(str(d), "directory")]
        ):
            result = Clipboard._try_get_clip_file("mydir")

        # Directories are not matched (ftype != "file")
        assert result == "mydir"

    def test_returns_original_on_internal_exception(self):
        with patch.object(
            Clipboard, "_get_clipboard_files", side_effect=RuntimeError("boom")
        ):
            result = Clipboard._try_get_clip_file("file.txt")

        assert result == "file.txt"
