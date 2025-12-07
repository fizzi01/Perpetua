import os
from typing import Optional, Callable, Any

import win32clipboard
import win32con

from event.EventBus import EventBus
from network.stream.GenericStream import StreamHandler

from ._base import BaseClipboardListener, BaseClipboard, BaseClipboardController, ClipboardType


class Clipboard(BaseClipboard):

    def __init__(self, on_change: Optional[Callable[[str, ClipboardType], Any]] = None, poll_interval: float = 0.5,
                 content_types: Optional[list[ClipboardType]] = None):
        super().__init__(on_change, poll_interval, content_types)

    @staticmethod
    def _try_get_clip_file(file: str) -> str:
        """
        Os-specific logic to get a complete file path from clipboard content.
        """
        files = Clipboard._get_clipboard_files()
        if files:
            # search for the file in the clipboard files
            for path, ftype in files:
                if ftype == "file" and os.path.basename(path) == os.path.basename(file):
                    return path
        return file

    @staticmethod
    def _get_clipboard_files() -> Optional[list[str]]:
        """
        Os-specific logic to get files from clipboard content.
        """
        win32clipboard.OpenClipboard()
        try:
            # Controlla se il contenuto è di tipo CF_H ROP (file)
            if win32clipboard.IsClipboardFormatAvailable(win32con.CF_HDROP):
                # Ottieni i percorsi dei file copiati
                file_paths = win32clipboard.GetClipboardData(win32con.CF_HDROP)
                results = []
                for path in file_paths:
                    if os.path.isdir(path):
                        results.append((path, "directory"))
                    elif os.path.isfile(path):
                        results.append((path, "file"))
                    else:
                        results.append((path, "unknown"))
                return results
            else:
                return None
        finally:
            # Chiudi la clipboard
            win32clipboard.CloseClipboard()



class ClipboardListener(BaseClipboardListener):
    def __init__(self, event_bus: EventBus, stream_handler: StreamHandler, command_stream: StreamHandler,
                 clipboard=BaseClipboard):
        super().__init__(event_bus, stream_handler, command_stream, clipboard)


class ClipboardController(BaseClipboardController):
    def __init__(self, event_bus: EventBus, stream_handler: StreamHandler, clipboard=BaseClipboard):
        super().__init__(event_bus, stream_handler, clipboard)
