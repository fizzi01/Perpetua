"""
Logic to handle cursor visibility on Linux systems.
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
from wx import Size

from multiprocessing.connection import Connection

from event.bus import EventBus
from input.cursor import _base
from network.stream.handler import StreamHandler


class CursorHandlerWindow(_base.CursorHandlerWindow):
    BORDER_OFFSET = 1
    WINDOW_SIZE = Size(400, 400)

    def __init__(
        self,
        command_conn: Connection,
        result_conn: Connection,
        mouse_conn: Connection,
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

        self.panel = None

        self.SetTransparent(0)
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
            self._logger.error(f"Error forcing overlay ({e})")

    def HideOverlay(self):
        try:
            self.Iconize(True)
            self.RestorePreviousApp()
            self.Hide()
        except Exception as e:
            self._logger.error(f"Error hiding overlay ({e})")

    def RestorePreviousApp(self):
        pass  # Linux does it automatically when the overlay is hidden

    def handle_cursor_visibility(self, visible: bool):
        """
        Handle cursor visibility for Linux.
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
