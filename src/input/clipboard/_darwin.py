
#  Perpatua - open-source and cross-platform KVM software.
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
from typing import Optional, Callable, Any

from AppKit import NSPasteboard, NSFilenamesPboardType

from event.bus import EventBus
from network.stream.handler import StreamHandler
from utils.logging import get_logger
from . import _base
from ._base import ClipboardType


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
                # search for the file in the clipboard files
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
    def _get_clipboard_files():
        try:
            pb = NSPasteboard.generalPasteboard()
            types = pb.types()

            # Controlla se ci sono file o directory nella clipboard
            if NSFilenamesPboardType in types:
                file_paths = pb.propertyListForType_(NSFilenamesPboardType)
                results = []
                for path in file_paths:
                    if os.path.isdir(path):
                        results.append((path, "directory"))
                    elif os.path.isfile(path):
                        results.append((path, "file"))
                    else:
                        results.append((path, "unknown"))
                return results
            return None
        except AttributeError:
            return None
        except TypeError:
            return None
        except OSError:
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
