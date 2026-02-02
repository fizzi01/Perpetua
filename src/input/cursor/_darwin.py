"""
Logic to handle cursor visibility on macOS systems.
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

# Object-c Library
import objc
import Quartz

from Quartz import kCGMaximumWindowLevel  # ty:ignore[unresolved-import]

from AppKit import (
    NSApplication,  # ty:ignore[unresolved-import]
    NSWindowCollectionBehaviorCanJoinAllSpaces,  # ty:ignore[unresolved-import]
    NSScreenSaverWindowLevel,  # ty:ignore[unresolved-import]
    NSApplicationActivationPolicyAccessory,  # ty:ignore[unresolved-import]
    NSWorkspace,  # ty:ignore[unresolved-import]
    NSApplicationActivateIgnoringOtherApps,  # ty:ignore[unresolved-import]
    NSApplicationPresentationAutoHideDock,  # ty:ignore[unresolved-import]
    NSApplicationPresentationAutoHideMenuBar,  # ty:ignore[unresolved-import]
    NSWindowCollectionBehaviorStationary,  # ty:ignore[unresolved-import]
    NSWindowCollectionBehaviorFullScreenAuxiliary,  # ty:ignore[unresolved-import]
)

# Accessibility API


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

        # Obtain NSApplication instance
        NSApp = NSApplication.sharedApplication()
        # Set activation policy to Accessory to hide the icon in the Dock
        NSApp.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

        self.SetSizer(vbox)

        # Black background
        self.SetBackgroundColour(wx.Colour(10, 10, 10))


class CursorHandlerWindow(_base.CursorHandlerWindow):
    BORDER_OFFSET: int = 1
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
        )
        # Panel principale
        self.panel = wx.Panel(self)

        # Obtain NSApplication instance
        NSApp = NSApplication.sharedApplication()
        # Set activation policy to Accessory to hide the icon in the Dock
        NSApp.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

        self.previous_app = NSWorkspace.sharedWorkspace().frontmostApplication()
        self.previous_app_pid = self.previous_app.processIdentifier()

        self._create()

    def RestoreFocus(self, event):
        """
        Restore current window focus when mouse leaves the overlay.
        """
        self.ForceOverlay()

    def ForceOverlay(self):
        try:
            p = self._get_centered_coords()
            super().ForceOverlay()
            self.Move(pt=p)

            self.previous_app = NSWorkspace.sharedWorkspace().frontmostApplication()
            self.previous_app_pid = self.previous_app.processIdentifier()

            NSApp = NSApplication.sharedApplication()
            NSApp.setPresentationOptions_(
                NSApplicationPresentationAutoHideDock
                | NSApplicationPresentationAutoHideMenuBar
            )
            NSApp.activateIgnoringOtherApps_(True)

            window_ptr = self.GetHandle()

            ns_view = objc.objc_object(c_void_p=window_ptr)  # type: ignore
            ns_window = ns_view.window()
            ns_window.setLevel_(kCGMaximumWindowLevel + 1)
            ns_window.setCollectionBehavior_(
                NSWindowCollectionBehaviorCanJoinAllSpaces
                | NSWindowCollectionBehaviorFullScreenAuxiliary
                | NSWindowCollectionBehaviorStationary
            )
            ns_window.setIgnoresMouseEvents_(False)
            ns_window.makeKeyAndOrderFront_(None)
        except Exception as e:
            print(f"Error forcing overlay: {e}")

    def HideOverlay(self):
        try:
            NSApp = NSApplication.sharedApplication()
            NSApp.setPresentationOptions_(0)
            NSApp.activateIgnoringOtherApps_(False)

            window_ptr = self.GetHandle()
            ns_view = objc.objc_object(c_void_p=window_ptr)  # type: ignore
            ns_window = ns_view.window()
            ns_window.setLevel_(NSScreenSaverWindowLevel - 1)
            ns_window.setIgnoresMouseEvents_(False)
            ns_window.setCollectionBehavior_(0)

            super().HideOverlay()
        except Exception as e:
            print(f"Error hiding overlay: {e}")

    def RestorePreviousApp(self):
        try:
            if self.previous_app:
                self.previous_app.activateWithOptions_(
                    NSApplicationActivateIgnoringOtherApps
                )
            self.previous_app = None
            self.previous_app_pid = None
        except Exception as e:
            print(f"Error restoring previous app: {e}")

    def _force_recapture(self):
        if not self.mouse_captured_flag.is_set():
            return

        try:
            # Timer
            retry_count = 4
            retry_interval = 1  # ms

            self._recapture_timer = wx.Timer(self)
            self._recapture_attempts = 0
            self._recapture_max_attempts = retry_count

            def on_timer(event):
                try:
                    if self._recapture_attempts < self._recapture_max_attempts:
                        self._recapture_attempts += 1
                        self.ForceOverlay()
                    else:
                        self._recapture_timer.Stop()
                except Exception as e:
                    self._logger.error(f"Error during recapture attempt ({e})")

            self.Bind(wx.EVT_TIMER, on_timer, self._recapture_timer)
            self._recapture_timer.Start(retry_interval)

        except Exception as e:
            self._logger.error(f"Error during recapture attempt ({e})")

    def handle_cursor_visibility(self, visible: bool):
        """
        Handle cursor visibility.
        If visible is False, hide the cursor. If True, show the cursor.
        Implement platform-specific cursor hiding/showing here.
        """
        if not visible:
            cursor = wx.Cursor(wx.CURSOR_BLANK)
            self.SetCursor(cursor)
            Quartz.CGDisplayHideCursor(Quartz.CGMainDisplayID())  # ty:ignore[unresolved-attribute]
        else:
            self.SetCursor(wx.NullCursor)
            Quartz.CGDisplayShowCursor(Quartz.CGMainDisplayID())  # ty:ignore[unresolved-attribute]


class CursorHandlerWorker(_base.CursorHandlerWorker):
    RESULT_POLL_TIMEOUT = 1  # sec

    def __init__(
        self,
        event_bus: EventBus,
        stream: Optional[StreamHandler] = None,
        debug: bool = False,
        window_class=CursorHandlerWindow,
    ):
        super().__init__(event_bus, stream, debug, window_class)
