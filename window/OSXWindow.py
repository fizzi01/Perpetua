import threading
import time
from multiprocessing import Process, Pipe, Queue
from utils.Logging import Logger

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


class FullScreenTransparentWindow(NSObject):
    @objc_method
    def init(self):
        # Metodo di inizializzazione nativo Objective-C
        self = objc.super(FullScreenTransparentWindow, self).init()
        if self is None:
            return None
        self.window = None  # Inizializza l'attributo window a None
        return self

    @python_method
    def create_window(self) -> AppKit.NSWindow:
        if self.window is not None:
            self.window.close()
            self.window = None

        screen_frame = AppKit.NSScreen.mainScreen().frame()
        window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            screen_frame,
            NSWindowStyleMaskBorderless,
            NSBackingStoreBuffered,
            False
        )
        if window is None:
            return None

        window.setLevel_(AppKit.NSScreenSaverWindowLevel + 10000)  # Assicura che la finestra sia sopra tutto
        window.setOpaque_(False)
        window.setBackgroundColor_(NSColor.clearColor())
        window.setIgnoresMouseEvents_(False)  # Imposta True se vuoi che la finestra sia "pass-through"
        window.setCollectionBehavior_(AppKit.NSWindowCollectionBehaviorFullScreenPrimary)
        window.setFrame_display_(screen_frame, True)  # Assicura che la finestra sia a schermo intero
        window.makeKeyAndOrderFront_(None)  # Assicura che la finestra sia visibile
        window.setAcceptsMouseMovedEvents_(True)  # Accetta gli eventi del mouse
        window.setMovableByWindowBackground_(False)  # Imposta True se vuoi che la finestra sia spostabile
        window.setRestorable_(True)  # Imposta True se vuoi che la finestra sia ripristinabile
        window.setExcludedFromWindowsMenu_(
            True)  # Imposta True se vuoi che la finestra sia esclusa dal menu delle finestre
        window.setHasShadow_(False)
        window.setReleasedWhenClosed_(False)
        window.setCanHide_(False)
        window.setAlphaValue_(1.0)
        window.setHidesOnDeactivate_(False)
        window.setLevel_(AppKit.NSScreenSaverWindowLevel + 10000)  # Imposta il livello della finestra
        return window

    def minimize(self):
        with objc.autorelease_pool():
            if self.window:
                self.window.setLevel_(AppKit.NSNormalWindowLevel - 1)  # Imposta il livello della finestra
                self.window.setIgnoresMouseEvents_(True)  # Ignora gli eventi del mouse quando minimizzata
                self.window.orderOut_(None)  # Nasconde la finestra
                self.window.miniaturize_(None)
                NSCursor.unhide()  # Rende di nuovo visibile il cursore

    def maximize(self):
        with objc.autorelease_pool():
            if self.window:
                self.window.deminiaturize_(None)  # Rimuove la minimizzazione della finestra
                self.window.setIgnoresMouseEvents_(False)  # Riabilita gli eventi del mouse
                self.window.makeKeyAndOrderFront_(None) # Assicura che la finestra sia visibile
                self.window.setLevel_(AppKit.NSScreenSaverWindowLevel + 1000)
                # Forzare l'applicazione a prendere il focus
                NSApp.activateIgnoringOtherApps_(True)

                NSCursor.hide()  # Nasconde il cursore

    def show(self):
        with objc.autorelease_pool():
            # Se la finestra non esiste, crea una nuova finestra
            self.window = self.create_window()
            return self.window

    def close(self):
        # Minimizzare invece di chiudere completamente la finestra
        self.minimize()


class AppDelegate(NSObject):
    """Minimalist app delegate."""

    def applicationDidFinishLaunching_(self, notification):
        """Create a window programmatically, without a NIB file."""
        self.window = FullScreenTransparentWindow.alloc().init()
        self.window.show()

        # Expose window instance for external control
        global transparent_window_instance
        transparent_window_instance = self.window

    def applicationShouldTerminateAfterLastWindowClosed_(self, sender):
        pass

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
        self.queue = Queue()

        self.process = Process(target=self._start_window_app, args=(self.queue,), daemon=True)
        self.process.start()

        self.log = Logger.get_instance().log

    def _start_window_app(self, input_conn):
        """Start the window application and handle external commands."""
        window_controller_thread = threading.Thread(target=self._window_proc_controller, args=(input_conn,),
                                                    daemon=True)
        window_controller_thread.start()
        TransparentWindowApp().run()

    def _window_proc_controller(self, input_conn):
        """Controller thread to receive commands from the main process and control the window."""
        global transparent_window_instance
        while True:
            command = input_conn.get()
            if command == "minimize":
                if transparent_window_instance:
                    transparent_window_instance.minimize()
            elif command == "maximize":
                if transparent_window_instance:
                    transparent_window_instance.maximize()
            elif command == "show":
                if transparent_window_instance:
                    transparent_window_instance.show()
            elif command == "close":
                if transparent_window_instance:
                    transparent_window_instance.close()
            elif command == "stop":
                # Exit the controller thread
                if transparent_window_instance:
                    transparent_window_instance.close()
                break

    def send_command(self, command):
        """Send a command to the transparent window."""
        if command in ["minimize", "maximize", "close", "stop", "show"]:
            self.queue.put(command)

    def close(self):
        """Close the window and terminate the process."""
        self.send_command("close")
        # #self.process.terminate()
        # #self.process.join()
        self.log("[WINDOW] Window closed correctly")
        return True

    def show(self):
        self.send_command("show")

    def stop(self):
        self.send_command("stop")
        self.process.terminate()
        self.process.join()
        self.log("[WINDOW] Window stopped correctly")

    def minimize(self):
        self.send_command("minimize")

    def maximize(self):
        self.send_command("maximize")