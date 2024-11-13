import sys
import argparse
import threading
import datetime
import time
from multiprocessing import Process, Pipe
from typing import Optional

import AppKit
import objc
from AppKit import (
    NSApp,
    NSApplication,
    NSColor,
    NSObject,
    NSRunningApplication,
    NSApplicationActivateIgnoringOtherApps,
    NSCursor,
    NSWindowStyleMaskBorderless,
    NSBackingStoreBuffered,
)
from Foundation import NSMakeRect, NSMutableArray, NSProcessInfo
from objc import objc_method, python_method, super
from PyObjCTools import AppHelper

EDGE_INSET = 20
EDGE_INSETS = (EDGE_INSET, EDGE_INSET, EDGE_INSET, EDGE_INSET)
PADDING = 8
WINDOW_WIDTH = 800
WINDOW_HEIGHT = 600


class FullScreenTransparentWindow(NSObject):
    @python_method
    def create_window(self) -> AppKit.NSWindow:
        screen_frame = AppKit.NSScreen.mainScreen().frame()
        window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            screen_frame,
            NSWindowStyleMaskBorderless,
            NSBackingStoreBuffered,
            False
        )
        if window is None:
            return None

        window.setLevel_(AppKit.NSStatusWindowLevel + 1)  # Assicura che la finestra sia sopra tutto, compreso il menu
        window.setOpaque_(False)
        window.setBackgroundColor_(NSColor.clearColor())
        window.setIgnoresMouseEvents_(False)  # Imposta True se vuoi che la finestra sia "pass-through"
        window.setCollectionBehavior_(AppKit.NSWindowCollectionBehaviorFullScreenPrimary)
        window.setFrame_display_(screen_frame, True)  # Assicura che la finestra sia a schermo intero
        NSCursor.hide()  # Nasconde il cursore
        return window

    def show(self):
        with objc.autorelease_pool():
            # create the window
            self.window = self.create_window()
            # finish setting up the window
            self.window.makeKeyAndOrderFront_(None)
            NSCursor.hide()
            self.window.setIgnoresMouseEvents_(False)
            self.window.setIsVisible_(True)
            self.window.makeKeyAndOrderFront_(None)
            self.window.setIsVisible_(True)
            self.window.setLevel_(AppKit.NSNormalWindowLevel + 1)
            self.window.setReleasedWhenClosed_(False)
            return self.window

    def minimize(self):
        with objc.autorelease_pool():
            # Cursore visibile e finestra assente
            NSCursor.unhide()
            self.window.setIsVisible_(False)
            self.window.setIgnoresMouseEvents_(True)
            self.window.setLevel_(AppKit.NSNormalWindowLevel - 1)

    def maximize(self):
        if hasattr(self, 'window') and self.window is not None:
            self.window.deminiaturize_(None)
            NSCursor.hide()
            self.window.setIsVisible_(True)
            self.window.setIgnoresMouseEvents_(False)
            self.window.setLevel_(AppKit.NSStatusWindowLevel + 1)

    def close(self):
        if hasattr(self, 'window') and self.window is not None:
            self.window.close()
            NSCursor.unhide()
            self.window = None


class AppDelegate(NSObject):
    """Minimalist app delegate."""

    def applicationDidFinishLaunching_(self, notification):
        """Create a window programmatically, without a NIB file."""
        self.window = FullScreenTransparentWindow.alloc().init()
        self.window.show()
        self.window.minimize()
        # Expose window instance for external control
        global transparent_window_instance
        transparent_window_instance = self.window

    def applicationShouldTerminateAfterLastWindowClosed_(self, sender):
        return True

    def get_window(self):
        return self.window


class TransparentWindowApp:
    """Create a minimalist app to test the transparent fullscreen window."""

    def run(self):
        with objc.autorelease_pool():
            # create the app
            NSApplication.sharedApplication()
            NSApp.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)  # No dock icon

            # create the delegate and attach it to the app
            delegate = AppDelegate.alloc().init()
            NSApp.setDelegate_(delegate)

            # run the app
            NSApp.activateIgnoringOtherApps_(True)

            # Use AppHelper.runEventLoop() to run the app instead of NSApp.run() to let pyobjc handle the event loop
            AppHelper.runEventLoop(installInterrupt=True)


# Global instance of the transparent window, used to control from external functions
transparent_window_instance = None


class HiddenWindow:
    def __init__(self, root=None):
        self.output_conn, self.input_conn = Pipe(duplex=False)
        self.process = Process(target=self._start_window_app, args=(self.output_conn,), daemon=True)
        self.process.start()

    def _start_window_app(self, input_conn):
        """Start the window application and handle external commands."""
        window_controller_thread = threading.Thread(target=self._window_proc_controller, args=(input_conn,),
                                                    daemon=True)
        window_controller_thread.start()
        TransparentWindowApp().run()

    def _window_proc_controller(self, input_conn):
        """Controller thread to receive commands from the main process and control the window."""
        print("External control started")
        while True:
            command = input_conn.recv()
            if command == "minimize":
                if transparent_window_instance:
                    transparent_window_instance.minimize()
                    print("Minimized window")
            elif command == "maximize":
                if transparent_window_instance:
                    transparent_window_instance.maximize()
                    print("Maximized window")
            elif command == "close":
                if transparent_window_instance:
                    transparent_window_instance.close()
                    print("Closed window")
                    # Exit the controller thread
                    break

    def send_command(self, command):
        """Send a command to the transparent window."""
        if command in ["minimize", "maximize", "close"]:
            self.input_conn.send(command)

    def close(self):
        """Close the window and terminate the process."""
        self.send_command("close")
        self.process.terminate()
        self.process.join()
        return True

    def minimize(self):
        self.send_command("minimize")

    def maximize(self):
        self.send_command("maximize")
