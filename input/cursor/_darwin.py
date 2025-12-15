"""
Logic to handle cursor visibility on macOS systems.
"""
from queue import Empty
from typing import Optional

import wx

from multiprocessing import Queue
from multiprocessing.connection import Connection

# Object-c Library
import objc
import Quartz

from Quartz import kCGMaximumWindowLevel


from AppKit import (
    NSCursor,
    NSApplication,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSScreenSaverWindowLevel,
    NSApplicationActivationPolicyAccessory,
    NSWorkspace,
    NSApplicationActivateIgnoringOtherApps,
    NSApplicationPresentationAutoHideDock,
    NSApplicationPresentationAutoHideMenuBar,
    NSWindowCollectionBehaviorParticipatesInCycle,
    NSWindowCollectionBehaviorStationary,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowCollectionBehaviorMoveToActiveSpace
)

# Accessibility API
from ApplicationServices import (
    AXUIElementCreateApplication,
    AXUIElementCopyAttributeValue,
    AXUIElementSetAttributeValue,
    kAXWindowsAttribute
)


from event.bus import EventBus
from input.cursor import _base
from network.stream import StreamHandler


class DebugOverlayPanel(wx.Panel):
    def __init__(self, parent):
        super().__init__(parent)

        # Layout
        vbox = wx.BoxSizer(wx.VERTICAL)

        # Titolo
        title = wx.StaticText(self, label="Test Mouse Capture Window")
        title.SetForegroundColour(wx.WHITE)
        title.SetFont(wx.Font(16, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
        vbox.Add(title, 0, wx.ALL | wx.CENTER, 10)

        # Info
        self.info_text = wx.StaticText(self, label="Premi SPAZIO per attivare/disattivare la cattura")
        self.info_text.SetForegroundColour(wx.Colour(200, 200, 200))
        vbox.Add(self.info_text, 0, wx.ALL | wx.CENTER, 5)

        # Stato
        self.status_text = wx.StaticText(self, label="Mouse Capture: DISATTIVO")
        self.status_text.SetForegroundColour(wx.Colour(255, 100, 100))
        self.status_text.SetFont(wx.Font(12, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
        vbox.Add(self.status_text, 0, wx.ALL | wx.CENTER, 10)


        # Delta display
        self.delta_text = wx.StaticText(self, label="Delta X: 0, Delta Y: 0")
        self.delta_text.SetForegroundColour(wx.WHITE)
        self.delta_text.SetFont(wx.Font(10, wx.FONTFAMILY_MODERN, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
        vbox.Add(self.delta_text, 0, wx.ALL | wx.CENTER, 5)

        # Istruzioni
        instructions = wx.StaticText(self,
                                     label="SPAZIO: Toggle capture\nESC: Disattiva | Q: Esci")
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

    def __init__(self, command_queue: Queue, result_queue:  Queue, mouse_conn: Connection, debug: bool = False):
        super().__init__(command_queue, result_queue, mouse_conn, debug, size=(400, 400))
        # Panel principale
        self.panel = DebugOverlayPanel(self)

        # Obtain NSApplication instance
        NSApp = NSApplication.sharedApplication()
        # Set activation policy to Accessory to hide the icon in the Dock
        NSApp.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

        self.previous_app = NSWorkspace.sharedWorkspace().frontmostApplication()
        self.previous_app_pid = self.previous_app.processIdentifier()

        self._create()

    def ForceOverlay(self):
        try:
            super().ForceOverlay()

            self.previous_app = NSWorkspace.sharedWorkspace().frontmostApplication()
            self.previous_app_pid = self.previous_app.processIdentifier()

            NSApp = NSApplication.sharedApplication()
            NSApp.setPresentationOptions_(
                NSApplicationPresentationAutoHideDock | NSApplicationPresentationAutoHideMenuBar)
            NSApp.activateIgnoringOtherApps_(True)

            window_ptr = self.GetHandle()

            ns_view = objc.objc_object(c_void_p=window_ptr) #type: ignore
            ns_window = ns_view.window()
            ns_window.setLevel_(kCGMaximumWindowLevel + 1)
            ns_window.setCollectionBehavior_(
                NSWindowCollectionBehaviorCanJoinAllSpaces | NSWindowCollectionBehaviorFullScreenAuxiliary | NSWindowCollectionBehaviorStationary)
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
            ns_view = objc.objc_object(c_void_p=window_ptr) #type: ignore
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
                self.previous_app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
            self.previous_app = None
            self.previous_app_pid = None
        except Exception as e:
            print(f"Error restoring previous app: {e}")

    def on_key_press(self, event):
        key_code = event.GetKeyCode()

        if self._debug:
            if key_code == wx.WXK_SPACE:
                if self.mouse_captured:
                    self.disable_mouse_capture()
                else:
                    self.enable_mouse_capture()
            elif key_code == wx.WXK_ESCAPE:
                self.disable_mouse_capture()
            elif key_code == ord('Q') or key_code == ord('q'):
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
        if not visible:
            cursor = wx.Cursor(wx.CURSOR_BLANK)
            self.SetCursor(cursor)
            Quartz.CGDisplayHideCursor(Quartz.CGMainDisplayID())
        else:
            self.SetCursor(wx.NullCursor)
            Quartz.CGDisplayShowCursor(Quartz.CGMainDisplayID())

    def update_ui(self, panel_obj, data, call):
        try:
            call(data)
        except Exception as e:
            pass


class CursorHandlerWorker(_base.CursorHandlerWorker):
    def __init__(self, event_bus: EventBus, stream: Optional[StreamHandler] = None, debug: bool = False,
                 window_class=CursorHandlerWindow):
        super().__init__(event_bus, stream, debug, window_class)