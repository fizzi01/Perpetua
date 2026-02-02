"""
Logic to handle cursor visibility on Windows systems.
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

from typing import Optional
import wx

from multiprocessing.connection import PipeConnection

from event.bus import EventBus
from input.cursor import _base
from network.stream.handler import StreamHandler


class DebugOverlayPanel(wx.Panel):
    def __init__(self, parent):
        super().__init__(parent)

        # Layout
        vbox = wx.BoxSizer(wx.VERTICAL)

        # Titolo
        title = wx.StaticText(self, label="Test Mouse Capture Window")
        title.SetForegroundColour(wx.WHITE)
        title.SetFont(
            wx.Font(16, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD)
        )
        vbox.Add(title, 0, wx.ALL | wx.CENTER, 10)

        # Info
        self.info_text = wx.StaticText(
            self, label="Premi SPAZIO per attivare/disattivare la cattura"
        )
        self.info_text.SetForegroundColour(wx.Colour(200, 200, 200))
        vbox.Add(self.info_text, 0, wx.ALL | wx.CENTER, 5)

        # Stato
        self.status_text = wx.StaticText(self, label="Mouse Capture: DISATTIVO")
        self.status_text.SetForegroundColour(wx.Colour(255, 100, 100))
        self.status_text.SetFont(
            wx.Font(12, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD)
        )
        vbox.Add(self.status_text, 0, wx.ALL | wx.CENTER, 10)

        # Delta display
        self.delta_text = wx.StaticText(self, label="Delta X: 0, Delta Y: 0")
        self.delta_text.SetForegroundColour(wx.WHITE)
        self.delta_text.SetFont(
            wx.Font(10, wx.FONTFAMILY_MODERN, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL)
        )
        vbox.Add(self.delta_text, 0, wx.ALL | wx.CENTER, 5)

        # Istruzioni
        instructions = wx.StaticText(
            self, label="SPAZIO: Toggle capture\nESC: Disattiva | Q: Esci"
        )
        instructions.SetForegroundColour(wx.Colour(150, 150, 150))
        vbox.Add(instructions, 0, wx.ALL | wx.CENTER, 20)

        self.SetSizer(vbox)

        # Black background
        self.SetBackgroundColour(wx.Colour(0, 0, 0))


class CursorHandlerWindow(_base.CursorHandlerWindow):
    BORDER_OFFSET = 1
    WINDOW_SIZE = (200, 200)

    def __init__(
        self,
        command_conn: PipeConnection,
        result_conn: PipeConnection,
        mouse_conn: PipeConnection,
        debug: bool = False,
        log_level: int = _base.Logger.DEBUG,
    ):
        super().__init__(
            command_conn,
            result_conn,
            mouse_conn,
            debug,
            log_level=log_level,
            size=self.WINDOW_SIZE,
            style=wx.STAY_ON_TOP | wx.FRAME_NO_TASKBAR | wx.NO_BORDER,
        )
        self.__size = self.WINDOW_SIZE
        # Panel principale
        self.panel = None

        # Windows-specific handle
        self.hwnd = None

        self._old_style = self.GetWindowStyle()
        # self.SetWindowStyle(
        #     self._old_style | wx.STAY_ON_TOP | wx.FRAME_NO_TASKBAR | wx.NO_BORDER  | wx.TRANSPARENT_WINDOW)
        self.SetTransparent(1)
        self._create()

    def ForceOverlay(self):
        try:
            self.Iconize(False)
            self.AcceptsFocusRecursively()

            p = self._get_centered_coords()
            self.Show(True)
            self.Move(pt=p)
            self.Raise()
        except Exception as e:
            print(f"Error forcing overlay: {e}")

    def HideOverlay(self):
        try:
            self.Iconize(True)
            super().HideOverlay()
        except Exception as e:
            print(f"Error hiding overlay: {e}")

    def RestorePreviousApp(self):
        return

    def handle_cursor_visibility(self, visible: bool):
        """
        Handle cursor visibility for Windows.
        If visible is False, hide the cursor. If True, show the cursor.
        """
        if not visible:
            # Hide cursor using wx
            cursor = wx.Cursor(wx.CURSOR_BLANK)
            self.SetCursor(cursor)
        else:
            # Show cursor
            self.SetCursor(wx.NullCursor)


class CursorHandlerWorker(_base.CursorHandlerWorker):
    RESULT_POLL_TIMEOUT = 1  # sec
    DATA_POLL_TIMEOUT = 0.01

    def __init__(
        self,
        event_bus: EventBus,
        stream: Optional[StreamHandler] = None,
        debug: bool = False,
        window_class=CursorHandlerWindow,
    ):
        super().__init__(event_bus, stream, debug, window_class)
