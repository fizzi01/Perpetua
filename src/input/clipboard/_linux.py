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
import subprocess
from urllib.parse import urlparse, unquote
from typing import Optional, Callable, Any

from event.bus import EventBus
from network.stream.handler import StreamHandler
from utils.logging import get_logger
from . import _base
from ._base import ClipboardType


# TODO: Extend copykitten to support get_files natively (already done in rust side)
class Clipboard(_base.Clipboard):
    __logger = get_logger(__name__)

    def __init__(
        self,
        on_change: Optional[Callable[[str, ClipboardType], Any]] = None,
        poll_interval: float = 0.5,
        content_types: Optional[list[ClipboardType]] = None,
    ):
        super().__init__(on_change, poll_interval, content_types)

    @staticmethod
    def _try_get_clip_file(file: str) -> str:
        """
        Os-specific logic to get a complete file path from clipboard content.
        """
        try:
            files = Clipboard._get_clipboard_files()
            if files:
                for path, ftype in files:
                    if ftype == "file" and os.path.basename(path) == os.path.basename(
                        file
                    ):
                        return path
            return file
        except Exception as e:
            Clipboard.__logger.critical(
                f"Could not retrieve files from clipboard -> {e}"
            )
            return file

    @staticmethod
    def _get_clipboard_files() -> Optional[list[tuple[str, str]]]:
        """
        Retrieve file paths from the Linux clipboard (X11 or Wayland).

        Tries xclip (X11) first, then falls back to wl-paste (Wayland).
        Parses text/uri-list MIME type which is the standard for file clipboard content.

        Returns:
            List of (path, type) tuples where type is "file", "directory", or "unknown",
            or None if no files are in the clipboard.
        """
        raw = Clipboard._read_uri_list_x11() or Clipboard._read_uri_list_wayland()
        if not raw:
            return None

        results = []
        for line in raw.splitlines():
            line = line.strip()
            # URI list comments and empty lines
            if not line or line.startswith("#"):
                continue
            parsed = urlparse(line)
            if parsed.scheme != "file":
                continue
            path = unquote(parsed.path)
            if os.path.isdir(path):
                results.append((path, "directory"))
            elif os.path.isfile(path):
                results.append((path, "file"))
            else:
                results.append((path, "unknown"))

        return results if results else None

    @staticmethod
    def _read_uri_list_x11() -> Optional[str]:
        """
        Read text/uri-list from the X11 clipboard using xclip.
        """
        try:
            result = subprocess.run(
                ["xclip", "-o", "-selection", "clipboard", "-t", "text/uri-list"],
                capture_output=True,
                timeout=1,
            )
            if result.returncode == 0 and result.stdout:
                return result.stdout.decode("utf-8", errors="ignore")
            return None
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return None

    @staticmethod
    def _read_uri_list_wayland() -> Optional[str]:
        """
        Read text/uri-list from the Wayland clipboard using wl-paste.
        """
        try:
            result = subprocess.run(
                ["wl-paste", "--no-newline", "--type", "text/uri-list"],
                capture_output=True,
                timeout=1,
            )
            if result.returncode == 0 and result.stdout:
                return result.stdout.decode("utf-8", errors="ignore")
            return None
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return None


class ClipboardListener(_base.ClipboardListener):
    def __init__(
        self,
        event_bus: EventBus,
        stream_handler: StreamHandler,
        command_stream: StreamHandler,
    ):
        super().__init__(
            event_bus, stream_handler, command_stream, Clipboard
        )  # We impose the clipboard core class here


class ClipboardController(_base.ClipboardController):
    def __init__(
        self, event_bus: EventBus, stream_handler: StreamHandler, clipboard: Clipboard
    ):
        super().__init__(event_bus, stream_handler, clipboard)
