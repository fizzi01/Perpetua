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

    def enable_mouse_capture(self):
        """Re-anchor ``center_pos`` to the centre of the
        ``overlay ∩ display`` intersection so the warp target stays
        strictly inside the active display.

        Why we override (Windows only): ``WarpPointer`` does NOT
        dissociate the visible cursor from OS-level clamping the way
        ``CGDisplayHideCursor`` does on macOS. When the cursor enters
        the server at an edge, the overlay (anchored on the cursor,
        see :meth:`_base.CursorHandlerWindow._get_centered_coords`)
        extends past the display bound; the base class warps to the
        overlay's GEOMETRIC centre, which lands ON the display edge.
        Every subsequent push outward is then silently clamped by the
        OS — ``EVT_MOTION`` reads ``delta = 0`` along the clamped axis
        and forwards nothing to the client. The user perceives the
        cursor as "stuck" or moving only along the edge.

        Why ONLY here (not in ``reset_mouse_position``): the visible
        centre is constant for the lifetime of a capture session (the
        overlay doesn't move once placed). Recomputing it on every
        ``on_mouse_move`` tick — which fires hundreds of times per
        second under heavy mouse use — adds two syscalls
        (``wx.GetMousePosition`` + ``wx.Display.GetFromPoint``) and a
        rectangle intersection per delta event, breaking the fluid
        warp-and-track loop. By patching ``center_pos`` ONCE at the
        end of ``enable_mouse_capture``, the base ``reset_mouse_position``
        keeps reading the cached value with zero overhead.
        """
        super().enable_mouse_capture()
        if not self.mouse_captured_flag.is_set():
            return
        visible_center = self._compute_visible_center()
        if visible_center is None or visible_center == self.center_pos:
            return
        self.center_pos = visible_center
        try:
            client_center = self.ScreenToClient(visible_center)
            self.WarpPointer(client_center.x, client_center.y)
        except Exception as e:
            self._logger.debug(f"visible-centre warp failed ({e})")

    def _compute_visible_center(self) -> Optional[Point]:
        """Centre of the overlay intersected with the display under
        the overlay. ``None`` if the overlay is entirely off-screen
        (caller falls back to the base ``center_pos``).

        Resolved against the overlay's own anchor point — NOT
        ``wx.GetMousePosition()`` — so the result is stable regardless
        of where the cursor is at compute time.
        """
        try:
            overlay_pos = self.GetPosition()
            overlay_size = self.GetSize()
            anchor = Point(
                overlay_pos.x + overlay_size.x // 2,
                overlay_pos.y + overlay_size.y // 2,
            )
            display_index = wx.Display.GetFromPoint(anchor)
            if display_index == wx.NOT_FOUND:
                display_index = 0
            display = wx.Display(display_index)
            screen_rect = display.GetClientArea()

            left = max(screen_rect.x, overlay_pos.x)
            top = max(screen_rect.y, overlay_pos.y)
            right = min(
                screen_rect.x + screen_rect.width,
                overlay_pos.x + overlay_size.x,
            )
            bottom = min(
                screen_rect.y + screen_rect.height,
                overlay_pos.y + overlay_size.y,
            )
            if right <= left or bottom <= top:
                return None
            return Point((left + right) // 2, (top + bottom) // 2)
        except Exception as e:
            self._logger.debug(f"visible centre computation failed ({e})")
            return None

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
