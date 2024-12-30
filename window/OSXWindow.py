# OSXWindow.py
# Purpose:  This file contains the implementation of a transparent overlay for macOS.
#         - The overlay is created using wxPython and is used to stay on top of all other windows.
#         - It handles the fullscreen state of the application and the fullscreen state of the windows.
#         - It restores the fullscreen state of the windows when the overlay is hidden.
#         - It restores the previous application when the overlay is hidden.
#         - The overlay is created in a separate process to avoid blocking the main process.
#
# Author: Federico Izzi

# Standard Libraries
import multiprocessing
import threading
import time

# UI Library
import wx

# Object-c Library
import objc
from objc import objc_method

# Quartz API
from Quartz import kCGMaximumWindowLevel

# Foundation API
from Foundation import NSObject, NSNotificationCenter

# AppKit API
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

# Internal Libraries
from window.InterfaceWindow import AbstractHiddenWindow
from utils.Logging import Logger

# Accessibility API costants
kAXFullScreenAttribute = "AXFullScreen"


def overlay_process(conn):
    class WindowStateObserver(NSObject):
        @objc_method
        def init(self):
            self = objc.super(WindowStateObserver, self).init()
            if self is None:
                return None

            # Register for notifications
            self.notification_center = NSWorkspace.sharedWorkspace().notificationCenter()
            self.notification_center.addObserver_selector_name_object_(
                self,
                self.windowDidChangeScreen_,
                "NSWorkspaceActiveSpaceDidChangeNotification",
                None
            )
            return self

        @objc_method
        def windowDidChangeScreen_(self, notification):
            # Invoke ForceOverlay on the OverlayPanel instance
            if hasattr(self, "overlay_panel") and not self.overlay_panel.is_hide_invoked:
                self.overlay_panel.ShowOverlay()

    class OverlayPanel(wx.Panel):
        def __init__(self, parent):
            super().__init__(parent)

            self.previous_app = NSWorkspace.sharedWorkspace().frontmostApplication()
            self.previous_app_pid = self.previous_app.processIdentifier()
            self.fullscreen_windows = []

            # Set background color and transparency
            self.SetBackgroundColour(wx.TransparentColour)

            # Hide the cursor using macOS APIs
            NSCursor.hide()

            self.running = True
            self.pipe_thread = threading.Thread(target=self.listen_for_commands, args=(conn,))
            self.pipe_thread.daemon = True
            self.pipe_thread.start()

            self.force_event_bound = False

            # Hide invoked Event
            self.is_hide_invoked = False

            # Initialize window state observer
            self.window_state_observer = WindowStateObserver.alloc().init()
            self.window_state_observer.overlay_panel = self

            self.monitor_timer = wx.Timer(self)
            self.Bind(wx.EVT_TIMER, self.monitor_overlay, self.monitor_timer)

        def on_focus(self, event):
            self.ShowOverlay()

        def on_kill_focus(self, event):
            self.ShowOverlay()
            event.Skip()

        def monitor_overlay(self, event):
            window_ptr = self.GetHandle()
            ns_view = objc.objc_object(c_void_p=window_ptr)
            ns_window = ns_view.window()
            wx.CallAfter(NSCursor.hide)
            if not ns_window.isKeyWindow():
                wx.CallAfter(self.ShowOverlay)

        def listen_for_commands(self, channel):
            while self.running:
                if channel.poll(0.1):
                    try:
                        command = channel.recv()
                        if command == 'minimize':
                            wx.CallAfter(self.HideOverlay)
                        elif command == 'maximize':
                            wx.CallAfter(self.ShowOverlay)
                        elif command == 'stop':
                            wx.CallAfter(self.Close)
                            channel.close()
                            self.running = False
                        elif command == 'is_running':
                            channel.send(True)
                    except EOFError:
                        break

        def Close(self, force=False):
            self.RestorePreviousApp()
            self.window_state_observer.autorelease()
            self.monitor_timer.Stop()
            if self.force_event_bound:
                self.Unbind(wx.EVT_KILL_FOCUS, self.on_kill_focus)
                self.force_event_bound = False

            super().Close(force)
            self.Parent.Close(force)

        def HideOverlay(self):
            self.is_hide_invoked = True
            self.monitor_timer.Stop()

            if self.force_event_bound:
                self.Unbind(wx.EVT_KILL_FOCUS)
                self.force_event_bound = False

            self.Hide()
            self.Parent.Hide()
            NSCursor.unhide()
            self.RestorePreviousApp()

        def ShowOverlay(self):
            self.is_hide_invoked = False

            if not self.force_event_bound:
                self.Bind(wx.EVT_KILL_FOCUS, self.on_kill_focus)
                self.force_event_bound = True

            self.Show()
            self.Parent.Show()
            NSCursor.hide()
            self.ForceOverlay()

            self.monitor_timer.Start(10)

        def ForceOverlay(self):
            self.previous_app = NSWorkspace.sharedWorkspace().frontmostApplication()
            self.previous_app_pid = self.previous_app.processIdentifier()

            NSApp = NSApplication.sharedApplication()
            NSApp.setPresentationOptions_(
                NSApplicationPresentationAutoHideDock | NSApplicationPresentationAutoHideMenuBar)
            NSApp.activateIgnoringOtherApps_(True)

            window_ptr = self.GetHandle()

            ns_view = objc.objc_object(c_void_p=window_ptr)
            ns_window = ns_view.window()
            ns_window.setLevel_(kCGMaximumWindowLevel + 1)
            ns_window.setCollectionBehavior_(
                NSWindowCollectionBehaviorCanJoinAllSpaces | NSWindowCollectionBehaviorFullScreenAuxiliary | NSWindowCollectionBehaviorStationary)
            ns_window.setIgnoresMouseEvents_(False)
            ns_window.makeKeyAndOrderFront_(None)

        def RestorePreviousApp(self):
            if self.previous_app:
                self.previous_app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
            self.previous_app = None
            self.previous_app_pid = None



    class OverlayFrame(wx.Frame):
        def __init__(self):
            display = wx.Display(0)
            geometry = display.GetGeometry()
            display_x = geometry.GetX()
            display_y = geometry.GetY()
            display_width = geometry.GetWidth()
            display_height = geometry.GetHeight()

            # Obtain NSApplication instance
            NSApp = NSApplication.sharedApplication()

            # Set activation policy to Accessory to hide the icon in the Dock
            NSApp.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

            wx.Frame.__init__(
                self,
                None,
                title="",
                pos=(display_x, display_y),
                size=(display_width, display_height),
            )

            self.SetTransparent(0)

            self.panel = OverlayPanel(self)

            parent_ptr = self.GetHandle()
            ns_view = objc.objc_object(c_void_p=parent_ptr)
            ns_window = ns_view.window()
            ns_window.setCollectionBehavior_(
                NSWindowCollectionBehaviorCanJoinAllSpaces | NSWindowCollectionBehaviorFullScreenAuxiliary | NSWindowCollectionBehaviorStationary)

            self.Show()

    app = wx.App()
    OverlayFrame()
    app.MainLoop()
    conn.close()


class HiddenWindow(AbstractHiddenWindow):
    def __init__(self):
        self.parent_conn, self.child_conn = multiprocessing.Pipe()

        self.process: multiprocessing.Process | None = None
        self.log = Logger.get_instance().log

    def start(self):
        self._start_window_app()

    def _start_window_app(self):
        """Start the window application and handle external commands."""
        self.process = multiprocessing.Process(target=overlay_process, args=(self.child_conn,))
        self.process.start()

    def _window_proc_controller(self, input_conn, output_conn):
        pass

    def send_command(self, command):
        """Send a command to the transparent window."""
        if command in ["minimize", "maximize", "close", "stop", "show", "is_running"]:
            try:
                self.parent_conn.send(command)
                return True
            # Catch if the pipe is closed
            except BrokenPipeError:
                return False
            except EOFError:
                return False

    def close(self):
        """Close the window and terminate the process."""
        self.send_command("stop")
        # #self.process.terminate()
        # #self.process.join()
        self.log("[WINDOW] Window closed correctly")
        return True

    def show(self):
        self.send_command("maximize")

    def stop(self):
        self.send_command("stop")
        self.process.terminate()
        self.process.join()
        self.log("[WINDOW] Window stopped correctly")

    def minimize(self):
        self.send_command("minimize")

    def maximize(self):
        self.send_command("maximize")

    def wait(self, timeout=5):
        timeout += time.time()
        while time.time() < timeout:
            return self.send_command("is_running")
        return False
