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

import sys
import os
import asyncio

import wx
from wx.core import Point, Size

import time
import threading

from multiprocessing import Pipe, Process

if sys.platform == "win32":
    from multiprocessing.connection import PipeConnection as Connection
else:
    from multiprocessing.connection import Connection
from typing import Optional

from event import (
    BusEventType,
    MouseEvent,
    ActiveScreenChangedEvent,
    ClientDisconnectedEvent,
    BusEvent,
)
from event.bus import EventBus

from network.stream.handler import StreamHandler

from utils.logging import get_logger, Logger
from utils.screen import Screen

wxEVT_SCREEN_UNLOCKED = wx.NewEventType()
EVT_SCREEN_UNLOCKED = wx.PyEventBinder(wxEVT_SCREEN_UNLOCKED, 1)


class CustomApp(wx.App):
    def OSXIsGUIApplication(self):
        return False

    def OnInit(self):
        # Screen.hide_icon()
        return True


class ScreenUnlockedEvent(wx.PyEvent):
    """Event to signal that the screen has been unlocked."""

    def __init__(self):
        super().__init__()
        self.SetEventType(wxEVT_SCREEN_UNLOCKED)


class CursorHandlerWindow(wx.Frame):
    """
    Base class for cursor handling window.
    Derived classes must implement platform-specific methods.
    """

    WINDOW_SIZE: Size = Size(400, 400)
    BORDER_OFFSET: int = 1
    # DATA_SEND_INTERVAL: float = 0.0005  # seconds
    LOCK_STATUS_CHECK_INTERVAL: int = 500  # ms

    def __init__(
        self,
        command_conn: Connection,
        result_conn: Connection,
        mouse_conn: Connection,
        debug: bool = False,
        log_level: int = Logger.DEBUG,
        **frame_kwargs,
    ):
        """
        Initialize the cursor handler window.
        Args:
            command_conn (Connection): Pipe connection for receiving commands.
            result_conn (Connection): Pipe connection for sending results.
            mouse_conn (Connection): Connection for sending mouse movement data.
            debug (bool): Enable debug mode.
            **frame_kwargs: Additional arguments for wx.Frame.
        """
        super().__init__(None, title="", **frame_kwargs)

        self._debug = debug
        self.mouse_captured_flag = threading.Event()
        self.mouse_captured_event = threading.Event()

        self.center_pos: Optional[Point] = None

        self.command_conn: Connection = command_conn
        self.result_conn: Connection = result_conn
        self.mouse_conn: Connection = mouse_conn

        self.previous_app = None
        self.previous_app_pid = None

        # Start command processing thread
        self._running = True
        self.command_thread = threading.Thread(target=self._process_commands)
        self.command_thread.start()

        # Main panel
        self.panel: Optional[wx.Panel] = (
            None  # Placeholder for derived classes to customize
        )

        if not self._debug:
            self.SetTransparent(0)

        # self.last_mouse_send_time = 0
        # self.mouse_send_interval_ns = int(self.DATA_SEND_INTERVAL * 1_000_000_000)
        # self.accumulated_delta_x = 0
        # self.accumulated_delta_y = 0

        # Screen lock monitoring
        self._screen_monitor_timer = wx.Timer(self)
        self._last_screen_locked_state: Optional[bool] = None
        self.Bind(
            wx.EVT_TIMER, self._on_screen_monitor_timer, self._screen_monitor_timer
        )

        # Events
        self.Bind(wx.EVT_MOTION, self.on_mouse_move)
        self.Bind(wx.EVT_CHAR_HOOK, self.on_key_press)
        self.Bind(wx.EVT_CLOSE, self._quit_app)
        self.Bind(wx.EVT_LEAVE_WINDOW, self.RestoreFocus)
        self.Bind(wx.EVT_MOUSE_CAPTURE_LOST, self.on_mouse_capture_lost)
        self.Bind(wx.EVT_KILL_FOCUS, self.on_kill_focus)
        self.Bind(wx.EVT_ACTIVATE, self.on_activate)
        self.Bind(EVT_SCREEN_UNLOCKED, self.on_screen_unlock)

        self._logger = get_logger(
            self.__class__.__name__, level=log_level, is_root=True
        )

    def _create(self):
        """
        Must be called by derived classes in constructor.
        """
        self.Centre()
        self.Show()
        self.HideOverlay(True)

    def _process_commands(self):
        """
        Commands processing loop.
        """
        try:
            while self._running:
                try:
                    if self.command_conn.poll(timeout=0.1):
                        command = self.command_conn.recv()
                        cmd_type = command.get("type")

                        if cmd_type == "enable_capture":
                            wx.CallAfter(self.enable_mouse_capture)
                        elif cmd_type == "disable_capture":
                            x, y = command.get("x", -1), command.get("y", -1)
                            wx.CallAfter(self.disable_mouse_capture, x, y)
                        elif cmd_type == "get_stats":
                            self.result_conn.send(
                                {
                                    "type": "stats",
                                    "is_captured": self.mouse_captured_flag.is_set(),
                                }
                            )
                        elif cmd_type == "quit":
                            self._running = False
                            wx.CallAfter(self._quit_app)
                except Exception:
                    # time.sleep(0)
                    continue
        except Exception as e:
            self._logger.error(f"Error processing commands ({e})")

    def _quit_app(self, event=None):
        """Quit the wx application properly"""

        # Unbind all events to prevent further processing
        self._logger.debug("Quitting")

        self.Unbind(wx.EVT_MOTION)
        self.Unbind(wx.EVT_CHAR_HOOK)
        self.Unbind(wx.EVT_CLOSE)
        self.Unbind(wx.EVT_LEAVE_WINDOW)
        self.Unbind(wx.EVT_TIMER)
        self.Unbind(wx.EVT_MOUSE_CAPTURE_LOST)
        self.Unbind(wx.EVT_KILL_FOCUS)
        self.Unbind(wx.EVT_ACTIVATE)
        self.Unbind(EVT_SCREEN_UNLOCKED)

        self._logger.debug("Stopping screen monitor...")
        try:
            self._stop_screen_monitor()
        except Exception as e:
            self._logger.debug(f"Error stopping screen monitor: {e}")
            pass

        self._logger.debug("Disabling mouse capture...")
        try:
            self.disable_mouse_capture()
        except Exception as e:
            self._logger.debug(f"Error disabling mouse capture: {e}")
            pass

        self._logger.debug("Destroying window...")
        try:
            if not self.IsBeingDeleted():
                self.Destroy()
        except Exception as e:
            self._logger.debug(f"Error destroying window ({e})")
            pass

        self._logger.debug("Exiting main loop...")
        try:
            app = wx.GetApp()
            if app:
                app.DeletePendingEvents()
                app.ExitMainLoop()
        except Exception as e:
            self._logger.debug(f"Error exiting main loop ({e})")
            pass

    def on_mouse_capture_lost(self, event):
        """
        Handle the EVT_MOUSE_CAPTURE_LOST event.
        This is called by wxWidgets when the mouse capture is lost.
        """
        # if self.mouse_captured_flag.is_set():
        # self._logger.warning(
        #     "EVT_MOUSE_CAPTURE_LOST received - capture was lost by system"
        # )
        event.Skip()

    def on_kill_focus(self, event):
        """
        Handle the EVT_KILL_FOCUS event.
        This is called when the window loses focus.
        """
        if self.mouse_captured_flag.is_set():
            # self._logger.warning(
            #     "EVT_KILL_FOCUS received - window lost focus while capture was active"
            # )
            # Schedule a re-focus and re-capture attempt
            wx.CallAfter(self._attempt_recapture)
        event.Skip()

    def on_activate(self, event):
        """
        Handle the EVT_ACTIVATE event.
        This is called when window activation state changes.
        """
        is_active = event.GetActive()
        if self.mouse_captured_flag.is_set() and not is_active:
            # self._logger.warning(
            #     f"EVT_ACTIVATE received - window deactivated (active={is_active}) while capture was active"
            # )
            # Schedule a re-focus and re-capture attempt
            wx.CallAfter(self._attempt_recapture)
        event.Skip()

    def on_screen_unlock(self, event):
        """
        Handle screen unlock event.
        This is called when the screen is unlocked after being locked.
        """
        if self.mouse_captured_flag.is_set():
            self._logger.info("Screen unlocked - attempting to recapture mouse")
            wx.CallAfter(self._attempt_recapture)
        event.Skip()

    def _attempt_recapture(self):
        """
        Attempt to recapture the mouse and restore focus.
        This is called when focus is lost while capture should be active.
        """
        if not self.mouse_captured_flag.is_set():
            return

        # self._logger.info("Attempting to recapture mouse and restore focus...")
        try:
            self.Raise()
            self.SetFocus()
            self.ForceOverlay()

            # self._logger.info("Recapture attempt completed")
        except Exception as e:
            self._logger.error(f"Error during recapture attempt ({e})")

    def RestoreFocus(self, event):
        """
        Restore current window focus when mouse leaves the overlay.
        Derived classes can implement platform-specific focus restoration here
        (default: do nothing).
        """
        if event:
            event.Skip()

    def ForceOverlay(self):
        """
        Force the overlay to be visible and interactive.
        """
        try:
            self.SetSize(self.WINDOW_SIZE)
            self.Show(True)
        except Exception as e:
            self._logger.debug(f"Error forcing overlay: {e}")

    def HideOverlay(self, startup: bool = False):
        """
        Hide the overlay and restore previous application (if implemented).
        """
        try:
            if not startup:
                self.RestorePreviousApp()
            # self.panel.Hide()
            self.Hide()
            # Resize to 0x0 to avoid interaction
            self.SetSize(Size(0, 0))
        except Exception as e:
            self._logger.debug(f"Error hiding overlay: {e}")

    def RestorePreviousApp(self):
        """
        Restore the previously active application.
        """
        raise NotImplementedError("Derived classes must implement RestorePreviousApp")

    def on_key_press(self, event):
        """
        Handle key press events for debug controls.
        """
        key_code = event.GetKeyCode()

        if self._debug:
            if key_code == wx.WXK_SPACE:
                if self.mouse_captured_flag.is_set():
                    self.disable_mouse_capture()
                else:
                    self.enable_mouse_capture()
            elif key_code == wx.WXK_ESCAPE:
                self.disable_mouse_capture()
            elif key_code == ord("Q") or key_code == ord("q"):
                self.Close()
            else:
                event.Skip()
        else:
            event.Skip()

    def handle_cursor_visibility(self, visible: bool):
        """
        Handle cursor visibility.
        If visible is False, hide the cursor. If True, show the cursor.
        Implement platform-specific cursor hiding/showing here.
        """
        raise NotImplementedError(
            "Derived classes must implement handle_cursor_visibility"
        )

    def MoveWindow(self, x: int = -1, y: int = -1) -> None:
        if x == -1 or y == -1:
            return

        # Denormalize coordinates
        screen_width, screen_height = wx.GetDisplaySize()
        x = int(x * screen_width)
        y = int(y * screen_height)

        try:
            self.Move(x, y)
        except Exception as e:
            self._logger.error(f"Error moving window ({e})")

    def _get_centered_coords(self) -> Point:
        """
        Get the coordinates to center the window on the cursor position.
        """
        cursor_pos = wx.GetMousePosition()

        display_index = wx.Display.GetFromPoint(cursor_pos)
        if display_index == wx.NOT_FOUND:
            display_index = 0
        display = wx.Display(display_index)
        screen_rect = display.GetClientArea()

        # Offset from border (in pixels)
        offset = self.BORDER_OFFSET

        # Calculate the position to center the window on the cursor
        x: int = cursor_pos.x - self.WINDOW_SIZE[0] // 2
        y: int = cursor_pos.y - self.WINDOW_SIZE[1] // 2

        # Apply limits considering the offset from the borders
        x: int = max(
            screen_rect.x + offset - self.WINDOW_SIZE[0] // 2,
            min(
                x, screen_rect.x + screen_rect.width - offset - self.WINDOW_SIZE[0] // 2
            ),
        )
        y: int = max(
            screen_rect.y + offset - self.WINDOW_SIZE[1] // 2,
            min(
                y,
                screen_rect.y + screen_rect.height - offset - self.WINDOW_SIZE[1] // 2,
            ),
        )
        return Point(x, y)

    def _force_recapture(self):
        """
        Attempt to recapture the mouse and restore focus.
        This is called every time capture is enabled, to ensure the overlay is focused.
        (On macOS in particular, focus may not be properly set on first attempt.)

        Os-specific implementations may override this method.
        """
        pass

    def enable_mouse_capture(self):
        """
        Enable mouse capture.
        """
        if not self.mouse_captured_flag.is_set():
            # Force focus before capturing
            self.Raise()
            self.SetFocus()
            self.ForceOverlay()
            wx.Sleep(0)

            # Hide the cursor
            self.handle_cursor_visibility(False)

            # Calculate the center of the window
            size = self.GetSize()
            pos = self.GetPosition()
            self.center_pos = Point(pos.x + size.width // 2, pos.y + size.height // 2)

            # Capture the mouse
            while not self.HasCapture():
                self.CaptureMouse()
            self.mouse_captured_flag.set()
            wx.Sleep(0)

            self._force_recapture()

            self.reset_mouse_position()
            self.result_conn.send({"type": "capture_enabled", "success": True})

            # Start screen lock monitoring
            wx.CallAfter(self._start_screen_monitor)

    def disable_mouse_capture(self, x: int = -1, y: int = -1):
        """
        Disable mouse capture.
        """
        if self.mouse_captured_flag.is_set():
            self.Unbind(wx.EVT_MOTION)
            self.MoveWindow(x, y)
            wx.Sleep(0)
            time.sleep(0)
            self.mouse_captured_flag.clear()
            wx.Sleep(0)
            time.sleep(0)

            # Release the mouse
            while self.HasCapture():
                self.ReleaseMouse()
            wx.Sleep(0)
            # wx.SafeYield()

            self.result_conn.send({"type": "capture_disabled", "success": True})

            # Restore the cursor
            self.HideOverlay()
            self.handle_cursor_visibility(True)
            wx.Sleep(0)

            self.Bind(wx.EVT_MOTION, self.on_mouse_move)  # Rebind MOTION event

            # Stop screen lock monitoring
            wx.CallAfter(self._stop_screen_monitor)

    def reset_mouse_position(self):
        """
        Reset mouse position to center.
        """
        if self.mouse_captured_flag.is_set() and self.center_pos is not None:
            # Move the cursor to the center of the window
            client_center = self.ScreenToClient(self.center_pos)
            self.WarpPointer(client_center.x, client_center.y)

    def on_mouse_move(self, event):
        """
        Handle mouse movement events.
        """
        if not self.mouse_captured_flag.is_set() or self.center_pos is None:
            time.sleep(0)
            event.Skip()
            return

        # Get current position
        mouse_pos = wx.GetMousePosition()

        # Calculate delta from the center
        delta_x = mouse_pos.x - self.center_pos.x
        delta_y = mouse_pos.y - self.center_pos.y

        # Process only if there is movement
        if delta_x != 0 or delta_y != 0:
            try:
                self.mouse_conn.send((delta_x, delta_y))
            except Exception:
                pass

            # Reset position
            time.sleep(0)
            self.reset_mouse_position()
            time.sleep(0)

        time.sleep(0)
        event.Skip()

    def on_close(self, event):
        """
        Handle window close event.
        """
        self._running = False
        self.disable_mouse_capture()
        self.Destroy()

    def _on_screen_monitor_timer(self, event):
        """
        Timer event handler for screen lock monitoring.
        Checks for screen lock/unlock transitions.
        """
        try:
            current_locked_state = Screen.is_screen_locked()

            # Detect transition from locked to unlocked
            if (
                self._last_screen_locked_state is not None
                and self._last_screen_locked_state
                and not current_locked_state
            ):
                self._logger.info("Screen unlock detected")
                # Post event to main thread
                wx.PostEvent(self, ScreenUnlockedEvent())

            self._last_screen_locked_state = current_locked_state

        except Exception as e:
            self._logger.error(f"Error in screen monitor timer ({e})")

    def _start_screen_monitor(self):
        """Start the screen lock monitoring thread."""
        if self._screen_monitor_timer.IsRunning():
            return

        self._last_screen_locked_state = Screen.is_screen_locked()
        self._screen_monitor_timer.Start(
            self.LOCK_STATUS_CHECK_INTERVAL
        )  # Check every 500 ms
        # self._logger.debug("Screen monitor started")

    def _stop_screen_monitor(self):
        """Stop the screen lock monitoring thread."""
        if not self._screen_monitor_timer.IsRunning():
            return

        self._screen_monitor_timer.Stop()
        # self._logger.debug("Screen monitor stopped")


class _CursorHandlerProcess:
    """
    Internal class to run the cursor handler window in a separate process.
    """

    @staticmethod
    def _cleanup(
        logger,
        command_conn: Connection,
        result_conn: Connection,
        mouse_conn: Connection,
    ):
        """Clean up pipes in child process"""
        try:
            # Drain connections
            while command_conn.poll():
                try:
                    command_conn.recv()
                except Exception:
                    break

            # Close resources
            command_conn.close()
            result_conn.close()
            mouse_conn.close()
        except Exception as e:
            logger.error(f"Error during cleanup ({e})")
            pass  # Ignore errors

    @staticmethod
    def run(
        command_conn: Connection,
        result_conn: Connection,
        mouse_conn: Connection,
        debug: bool = False,
        window_class=CursorHandlerWindow,
        log_level: int = Logger.INFO,
    ):
        """Run the cursor handler window process"""
        # wx.App grabs infos from bundle dict, so we need to call it before
        Screen.hide_icon()  # Suppress dock icon on macOS
        _logger = get_logger(
            "_CursorHandlerProcess",
            level=log_level,
            is_root=True,
        )

        _logger.debug("Starting...", pid=os.getpid())
        app = None
        window = None
        try:
            app = CustomApp()

            window = window_class(
                command_conn=command_conn,
                result_conn=result_conn,
                mouse_conn=mouse_conn,
                debug=debug,
                log_level=log_level,
            )

            # Notify that the window is ready
            result_conn.send({"type": "window_ready"})
            _logger.debug("Entering main loop")
            app.MainLoop()
            result_conn.send({"type": "process_ended"})
            _logger.debug("Main loop left")
        except Exception as e:
            _logger.error(f"{e}")
        finally:
            # Clean up wx resources first
            try:
                if window and not window.IsBeingDeleted():
                    window.Destroy()
            except Exception as e:
                _logger.error(f"Error destroying window ({e})")
                pass

            try:
                if app:
                    app.DeletePendingEvents()
                    app.ExitMainLoop()
            except Exception as e:
                _logger.error(f"Error exiting app main loop ({e})")
                pass

            # Then clean up IPC resources
            _CursorHandlerProcess._cleanup(
                _logger, command_conn, result_conn, mouse_conn
            )

            _logger.debug("Process exiting")


from ._worker import CursorHandlerWorker  # noqa: F401


class _WxCursorHandlerWorker(CursorHandlerWorker):
    """wx-based worker that uses _CursorHandlerProcess as the process target."""

    def _get_process_target(self):
        return _CursorHandlerProcess.run

    def _get_process_args(self):
        return (
            self.command_conn_rec,
            self.result_conn_send,
            self.mouse_conn_send,
            self._debug,
            self.window_class,
            self._logger.level,
        )
