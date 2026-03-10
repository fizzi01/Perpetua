"""
Cursor capture for Linux (X11 and Wayland).

X11: Xlib-based pointer grab with XFixes cursor hiding.
Wayland: libei InputCapture portal with snegg Receiver (GNOME >= 45, KDE >= 6.1).
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

import os
import select

from multiprocessing.connection import Connection
from typing import Optional

from Xlib import X, display as xdisplay, Xatom
from Xlib.xobject import cursor as xcursor

from event.bus import EventBus
from input._platform import is_wayland, is_gnome, is_kde
from input.cursor._worker import CursorHandlerWorker as _WorkerBase
from network.stream.handler import StreamHandler
from utils.logging import get_logger, Logger


class _XlibCursorHandler:
    """Xlib-based cursor handler running in a child process."""

    WINDOW_SIZE = 100
    BORDER_OFFSET = 0

    def __init__(
        self,
        command_conn: Connection,
        result_conn: Connection,
        mouse_conn: Connection,
        debug: bool = False,
        log_level: int = Logger.INFO,
    ):
        self._command_conn = command_conn
        self._result_conn = result_conn
        self._mouse_conn = mouse_conn
        self._debug = debug
        self._running = True
        self._captured = False
        self._center_x = 0
        self._center_y = 0
        self._skip_motion = False

        self._logger = get_logger("XlibCursorHandler", level=log_level, is_root=True)

        # Open X display
        self._display = xdisplay.Display()
        self._screen = self._display.screen()
        self._root = self._screen.root

        # Check XFixes extension for cursor hiding
        self._has_xfixes = False
        try:
            self._has_xfixes = self._display.has_extension("XFIXES")
            if self._has_xfixes:
                from Xlib.ext import xfixes  # noqa: F811

                xfixes.query_version(self._display)
        except Exception:
            self._has_xfixes = False

        # Create invisible cursor (fallback when XFixes is unavailable)
        self._blank_cursor = self._create_blank_cursor()

        # Create overlay window
        self._window = self._create_window()

    def _create_blank_cursor(self):
        """Create an invisible 1x1 pixmap cursor."""
        try:
            from Xlib.protocol import request

            pixmap = self._root.create_pixmap(1, 1, 1)
            gc = pixmap.create_gc(foreground=0)
            pixmap.fill_rectangle(gc, 0, 0, 1, 1)
            gc.free()

            cid = self._display.display.allocate_resource_id()
            request.CreateCursor(
                display=self._display.display,
                cid=cid,
                source=pixmap.id,
                mask=pixmap.id,
                fore_red=0,
                fore_green=0,
                fore_blue=0,
                back_red=0,
                back_green=0,
                back_blue=0,
                x=0,
                y=0,
            )
            pixmap.free()
            return xcursor.Cursor(self._display.display, cid, owner=1)
        except Exception as e:
            self._logger.warning(f"Failed to create blank pixmap cursor: {e}")
            # Fallback: glyph cursor with all-black colours
            try:
                font = self._display.open_font("cursor")
                cursor = font.create_glyph_cursor(font, 0, 1, (0, 0, 0), (0, 0, 0))
                font.close()
                return cursor
            except Exception:
                return X.NONE

    def _create_window(self):
        """Create an override-redirect window for mouse capture."""
        sw = self._screen.width_in_pixels
        sh = self._screen.height_in_pixels
        x = (sw - self.WINDOW_SIZE) // 2
        y = (sh - self.WINDOW_SIZE) // 2

        window = self._root.create_window(
            x,
            y,
            self.WINDOW_SIZE,
            self.WINDOW_SIZE,
            0,
            self._screen.root_depth,
            X.InputOutput,
            X.CopyFromParent,
            event_mask=(
                X.PointerMotionMask
                | X.KeyPressMask
                | X.StructureNotifyMask
                | X.FocusChangeMask
            ),
            override_redirect=True,
        )

        # Set window opacity to 0 (fully transparent)
        opacity_atom = self._display.intern_atom("_NET_WM_WINDOW_OPACITY")
        window.change_property(opacity_atom, Xatom.CARDINAL, 32, [0])

        return window

    def run(self):
        """Main event loop using select() on X fd and command pipe fd."""
        self._result_conn.send({"type": "window_ready"})

        x_fd = self._display.fileno()
        cmd_fd = self._command_conn.fileno()

        while self._running:
            try:
                readable, _, _ = select.select([x_fd, cmd_fd], [], [], 0.01)
            except (ValueError, OSError):
                break

            # Process X events
            if x_fd in readable or self._display.pending_events():
                while self._display.pending_events():
                    event = self._display.next_event()
                    self._handle_x_event(event)

            # Process pipe commands
            if cmd_fd in readable:
                self._process_commands()

        self._cleanup()
        try:
            self._result_conn.send({"type": "process_ended"})
        except Exception:
            pass

    def _handle_x_event(self, event):
        if event.type == X.MotionNotify:
            self._on_motion(event)
        elif event.type == X.FocusOut and self._captured:
            self._attempt_recapture()

    def _on_motion(self, event):
        if not self._captured:
            return

        # Skip stale motion events generated during grab/warp setup
        if self._skip_motion:
            self._skip_motion = False
            return

        dx = event.root_x - self._center_x
        dy = event.root_y - self._center_y

        if dx != 0 or dy != 0:
            try:
                self._mouse_conn.send((dx, dy))
            except Exception:
                pass

            # Warp pointer back to center
            self._root.warp_pointer(self._center_x, self._center_y)
            self._display.flush()

    def _process_commands(self):
        try:
            while self._command_conn.poll(0):
                command = self._command_conn.recv()
                cmd_type = command.get("type")

                if cmd_type == "enable_capture":
                    self._enable_capture()
                elif cmd_type == "disable_capture":
                    x = command.get("x", -1)
                    y = command.get("y", -1)
                    self._disable_capture(x, y)
                elif cmd_type == "get_stats":
                    self._result_conn.send(
                        {"type": "stats", "is_captured": self._captured}
                    )
                elif cmd_type == "quit":
                    self._running = False
        except (EOFError, BrokenPipeError):
            self._running = False
        except Exception as e:
            self._logger.error(f"Error processing command: {e}")

    def _enable_capture(self):
        if self._captured:
            return

        # Get current cursor position
        qp = self._root.query_pointer()

        # Position window centered on cursor
        wx = qp.root_x - self.WINDOW_SIZE // 2
        wy = qp.root_y - self.WINDOW_SIZE // 2

        # Clamp to screen
        sw = self._screen.width_in_pixels
        sh = self._screen.height_in_pixels
        wx = max(0, min(wx, sw - self.WINDOW_SIZE))
        wy = max(0, min(wy, sh - self.WINDOW_SIZE))

        self._window.configure(x=wx, y=wy)
        self._window.map()
        self._window.raise_window()
        self._display.sync()

        self._center_x = wx + self.WINDOW_SIZE // 2
        self._center_y = wy + self.WINDOW_SIZE // 2

        # Grab pointer
        status = self._window.grab_pointer(
            True,
            X.PointerMotionMask | X.ButtonPressMask | X.ButtonReleaseMask,
            X.GrabModeAsync,
            X.GrabModeAsync,
            X.NONE,
            self._blank_cursor,
            X.CurrentTime,
        )

        if status == X.GrabSuccess:
            # Hide cursor via XFixes if available
            if self._has_xfixes:
                try:
                    from Xlib.ext import xfixes

                    xfixes.hide_cursor(self._display)
                except Exception:
                    pass

            self._display.sync()
            self._captured = True

            # Warp to center
            self._root.warp_pointer(self._center_x, self._center_y)
            self._display.sync()

            # Drain stale motion events queued during grab/warp
            while self._display.pending_events():
                self._display.next_event()
            self._skip_motion = True

            self._result_conn.send({"type": "capture_enabled", "success": True})
        else:
            self._logger.error(f"Failed to grab pointer, status={status}")
            self._window.unmap()
            self._display.sync()
            self._result_conn.send({"type": "capture_enabled", "success": False})

    def _disable_capture(self, x: int = -1, y: int = -1):
        if not self._captured:
            return

        self._captured = False

        # Ungrab pointer
        self._display.ungrab_pointer(X.CurrentTime)

        # Show cursor via XFixes
        if self._has_xfixes:
            try:
                from Xlib.ext import xfixes

                xfixes.show_cursor(self._display)
            except Exception:
                pass

        # Move cursor to requested position (denormalize)
        if x != -1 and y != -1:
            sw = self._screen.width_in_pixels
            sh = self._screen.height_in_pixels
            abs_x = int(x * sw)
            abs_y = int(y * sh)
            self._root.warp_pointer(abs_x, abs_y)

        # Hide window
        self._window.unmap()
        self._display.sync()

        self._result_conn.send({"type": "capture_disabled", "success": True})

    def _attempt_recapture(self):
        if not self._captured:
            return

        try:
            self._window.raise_window()
            self._window.set_input_focus(X.RevertToParent, X.CurrentTime)

            self._window.grab_pointer(
                True,
                X.PointerMotionMask | X.ButtonPressMask | X.ButtonReleaseMask,
                X.GrabModeAsync,
                X.GrabModeAsync,
                X.NONE,
                self._blank_cursor,
                X.CurrentTime,
            )

            self._display.sync()
        except Exception as e:
            self._logger.error(f"Recapture failed: {e}")

    def _cleanup(self):
        if self._display is None:
            return
        try:
            if self._captured:
                self._display.ungrab_pointer(X.CurrentTime)
                if self._has_xfixes:
                    try:
                        from Xlib.ext import xfixes

                        xfixes.show_cursor(self._display)
                    except Exception:
                        pass
                self._captured = False
            self._window.destroy()
            self._display.flush()
            self._display.close()
        except Exception as e:
            self._logger.error(f"Cleanup error: {e}")
        finally:
            self._display = None


def _run_xlib_process(
    command_conn: Connection,
    result_conn: Connection,
    mouse_conn: Connection,
    debug: bool = False,
    log_level: int = Logger.INFO,
):
    """Process entry point for the Xlib cursor handler."""
    logger = get_logger("_XlibCursorProcess", level=log_level, is_root=True)
    logger.debug("Starting...", pid=os.getpid())

    handler = None
    try:
        handler = _XlibCursorHandler(
            command_conn, result_conn, mouse_conn, debug, log_level
        )
        handler.run()
    except Exception as e:
        logger.error(f"{e}")
    finally:
        if handler:
            handler._cleanup()

        # Clean up pipes
        try:
            while command_conn.poll():
                command_conn.recv()
        except Exception:
            pass

        try:
            command_conn.close()
            result_conn.close()
            mouse_conn.close()
        except Exception:
            pass

        logger.debug("Process exiting")


class CursorHandlerWorker(_WorkerBase):
    """Linux cursor handler worker.

    No-op on Wayland (capture is handled by ServerMouseListener).
    On X11 the Xlib-based handler is spawned as a child process.
    """

    RESULT_POLL_TIMEOUT = 1  # sec
    DATA_POLL_TIMEOUT = 0.01

    def __init__(
        self,
        event_bus: EventBus,
        stream: Optional[StreamHandler] = None,
        debug: bool = False,
        window_class=None,
    ):
        self._use_wayland = is_wayland() and (is_gnome() or is_kde())

        if self._use_wayland:
            # ServerMouseListener handles everything
            self._is_running = False
            return

        super().__init__(event_bus, stream, debug, window_class=None)

    async def start(self, wait_ready=True, timeout=None) -> bool:
        if self._use_wayland:
            self._is_running = True
            return True
        if timeout is None:
            timeout = 1
        return await super().start(wait_ready=wait_ready, timeout=timeout)

    async def stop(self, timeout=2):
        if self._use_wayland:
            self._is_running = False
            return
        return await super().stop(timeout=timeout)

    def is_alive(self) -> bool:
        if self._use_wayland:
            return self._is_running
        return super().is_alive()

    def _get_process_target(self):
        return _run_xlib_process

    def _get_process_args(self):
        return (
            self.command_conn_rec,
            self.result_conn_send,
            self.mouse_conn_send,
            self._debug,
            self._logger.level,
        )
