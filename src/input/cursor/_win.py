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
from wx import Point, Size
import win32gui

from multiprocessing.connection import PipeConnection

from event.bus import EventBus
from input.cursor import _base
from input.cursor._worker import CursorHandlerWorker as _WorkerBase
from network.stream.handler import StreamHandler


class CursorHandlerWindow(_base.CursorHandlerWindow):
    BORDER_OFFSET = 1
    WINDOW_SIZE = Size(200, 200)

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

        # Prev app track (for reopening)
        self.previous_window_handle = None

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

            if not self.previous_window_handle:
                self.previous_window_handle = win32gui.GetForegroundWindow()
        except Exception as e:
            self._logger.error(f"Error forcing overlay ({e})")

    def _get_centered_coords(self) -> Point:
        """Anchor the overlay at the centre of the display under the
        cursor instead of next to the cursor itself.

        ``WarpPointer`` on Windows does NOT dissociate the visible
        cursor from OS-level clamping the way ``CGDisplayHideCursor``
        does on macOS. If the warp target lands within
        ``WINDOW_SIZE/2`` px of a display edge, every subsequent user
        motion toward that edge is silently clamped by the OS and
        ``EVT_MOTION`` reports ``delta = 0`` along that axis. Anchoring
        the overlay at the display centre guarantees the warp target is
        always ``≥ WINDOW_SIZE/2`` px from every edge, so motion in all
        four directions is captured before the OS gets a chance to
        clamp it. The overlay is fully transparent in production builds
        so the position change is invisible to the user.
        """
        cursor_pos = wx.GetMousePosition()
        display_index = wx.Display.GetFromPoint(cursor_pos)
        if display_index == wx.NOT_FOUND:
            display_index = 0
        display = wx.Display(display_index)
        screen_rect = display.GetClientArea()
        x = screen_rect.x + (screen_rect.width - self.WINDOW_SIZE[0]) // 2
        y = screen_rect.y + (screen_rect.height - self.WINDOW_SIZE[1]) // 2
        return Point(x, y)

    def HideOverlay(self, startup: bool = False):
        try:
            self.Iconize(True)
            super().HideOverlay(startup)
        except Exception as e:
            self._logger.error(f"Error hiding overlay ({e})")

    def RestorePreviousApp(self):
        try:
            if self.previous_window_handle:
                win32gui.SetForegroundWindow(self.previous_window_handle)
            self.previous_window_handle = None
        except Exception as e:
            self._logger.error(f"Error restoring previous app ({e})")

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


class CursorHandlerWorker(_WorkerBase):
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

    def _get_process_target(self):
        return _base._CursorHandlerProcess.run

    def _get_process_args(self):
        return (
            self.command_conn_rec,
            self.result_conn_send,
            self.mouse_conn_send,
            self._debug,
            self.window_class,
            self._logger.level,
        )
