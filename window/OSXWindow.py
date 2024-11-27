import queue
import threading
import time
from multiprocessing import Process, Pipe, Queue
from utils.Logging import Logger
from window.InterfaceWindow import AbstractHiddenWindow

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
from objc import objc_method, python_method, super
from PyObjCTools import AppHelper

app_window = None

class FullScreenTransparentWindow(AppKit.NSWindow):
    @objc_method
    def canBecomeKeyWindow(self) -> bool:
        return True  # Forza la finestra a diventare key window


class WindowController(NSObject):
    @objc_method
    def init(self):
        # Metodo di inizializzazione nativo Objective-C
        self = objc.super(WindowController, self).init()
        if self is None:
            return None
        self.window = None  # Inizializza l'attributo window a None
        return self

    @python_method
    def create_window(self) -> AppKit.NSWindow:
        self.is_window_created = False  # Inizilization flag

        if self.window is not None:
            self.window.close()
            self.window = None

        screen_frame = AppKit.NSScreen.mainScreen().frame()
        window = FullScreenTransparentWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            screen_frame,
            NSWindowStyleMaskBorderless,
            NSBackingStoreBuffered,
            False
        )
        if window is None:
            return None

        #window.setLevel_(AppKit.NSScreenSaverWindowLevel + 10000)  # Assicura che la finestra sia sopra tutto
        print("Creating window")
        print(f"Screen frame: {screen_frame}")
        print(f"Setting opaque")
        window.setOpaque_(False)
        print(f"Setting background color")
        window.setBackgroundColor_(NSColor.clearColor())
        print(f"Setting ignores mouse events")
        window.setIgnoresMouseEvents_(False)  # Imposta True se vuoi che la finestra sia "pass-through"
        print(f"[CREATION] Setting collection behavior")
        window.setCollectionBehavior_(AppKit.NSWindowCollectionBehaviorFullScreenPrimary)
        print(f"[CREATION] Setting frame display")
        window.setFrame_display_(screen_frame, True)  # Assicura che la finestra sia a schermo intero
        print(f"[CREATION] Accepting mouse moved events")
        window.setAcceptsMouseMovedEvents_(True)  # Accetta gli eventi del mouse
        print(f"[CREATION] Setting movable by window background")
        window.setMovableByWindowBackground_(False)  # Imposta True se vuoi che la finestra sia spostabile
        print(f"[CREATION] Setting restorable")
        window.setRestorable_(True)  # Imposta True se vuoi che la finestra sia ripristinabile
        print(f"[CREATION] Setting excluded from windows menu")
        window.setExcludedFromWindowsMenu_(
            True)  # Imposta True se vuoi che la finestra sia esclusa dal menu delle finestre
        print(f"[CREATION] Setting has shadow")
        window.setHasShadow_(False)
        print(f"[CREATION] Setting released when closed")
        window.setReleasedWhenClosed_(False)
        print(f"[CREATION] Setting can hide")
        window.setCanHide_(False)
        print(f"[CREATION] Setting alpha value")
        window.setAlphaValue_(1.0)
        print(f"[CREATION] Setting hides on deactivate")
        window.setHidesOnDeactivate_(False)
        #window.setLevel_(AppKit.NSScreenSaverWindowLevel + 10000)  # Imposta il livello della finestra

        print(f"[CREATION] Setting window as key")
        window.makeKeyAndOrderFront_(None)  # Assicura che la finestra sia visibile

        print(f"[CREATION] Hiding cursor")
        NSCursor.hide()  # Nasconde il cursore

        print(f"[CREATION] Retaining window")
        print(f"[CREATION] Window created")

        global app_window
        app_window = window

        return window

    @objc_method
    def minimize(self):
        print("Minimizing window")
        if self.window:
            print("Window exists")

            print("[MINIMIZE] Miniaturize window")
            AppKit.NSApp.hide_(None)
            AppKit.NSApp.activateIgnoringOtherApps_(False)
            print("[MINIMIZE] Hiding cursor")
            NSCursor.unhide()  # Rende di nuovo visibile il cursore
            print(f"Setting window level to {AppKit.NSNormalWindowLevel}")
            self.window.setLevel_(AppKit.NSNormalWindowLevel)  # Imposta il livello della finestra

            self.window.setIgnoresMouseEvents_(True)  # Ignora gli eventi del mouse quando minimizzata

            self.window.orderOut_(None)  # Nasconde la finestra
            print(f"Is window key: {self.window.isKeyWindow()}")

    @python_method
    def maximize(self):
        print("Maximizing window")
        global app_window
        if app_window:
            print("[MAXIMIZE] Activating app")
            NSApp.activateIgnoringOtherApps_(True)
            print("[MAXIMIZE] Hiding cursor")
            NSCursor.hide()  # Nasconde il cursore
            #app_window.deminiaturize_(None)  # Rimuove la minimizzazione della finestra
            #app_window.setIgnoresMouseEvents_(False)  # Riabilita gli eventi del mouse
            #app_window.makeKeyAndOrderFront_(None) # Assicura che la finestra sia visibile
            #app_window.setLevel_(AppKit.NSScreenSaverWindowLevel + 1000)
            # Forzare l'applicazione a prendere il focus
            print(f"Is window key: {app_window.isKeyWindow()}")

    @python_method
    def show(self):
        # Se la finestra non esiste, crea una nuova finestra
        self.window = self.create_window()
        global app_window
        app_window = self.window

        return self.window

    @python_method
    def close(self):
        # Minimizzare invece di chiudere completamente la finestra
        self.minimize()

    @python_method
    def is_minimized(self):
        global app_window
        if app_window:
            return app_window.isMiniaturized()
        return False

    @python_method
    def is_window_running(self):
        with objc.autorelease_pool():
            return self.is_window_created

    @python_method
    def set_window_running(self, value):
        self.is_window_created = value


class AppDelegate(NSObject):
    """Minimalist app delegate."""

    def applicationDidFinishLaunching_(self, notification):
        """Create a window programmatically, without a NIB file."""
        self.window = WindowController.alloc().init()
        self.window.show()

        # Expose window instance for external control
        global transparent_window_instance
        transparent_window_instance = self.window
        transparent_window_instance.set_window_running(True)

    def applicationShouldTerminateAfterLastWindowClosed_(self, sender):
        return False

    def applicationShouldTerminate_(self, sender):
        print("Application should terminate.")
        return True

    def applicationWillTerminate_(self, notification):
        print(f"Application will terminate. {notification}")

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


class HiddenWindow(AbstractHiddenWindow):
    def __init__(self, root=None):
        self.input_queue = Queue()
        self.output_queue = Queue()

        self.process = Process(target=self._start_window_app, args=(self.input_queue,self.output_queue), daemon=True)
        self.process.start()

        self.log = Logger.get_instance().log

    def _start_window_app(self, input_conn, output_conn):
        """Start the window application and handle external commands."""
        window_controller_thread = threading.Thread(target=self._window_proc_controller, args=(input_conn, output_conn),
                                                    daemon=True)
        window_controller_thread.start()

        try:
            TransparentWindowApp().run()
        except Exception as e:
            print(f"Error in window app: {e}")

    def _window_proc_controller(self, input_conn, output_conn):
        """Controller thread to receive commands from the main process and control the window."""
        global transparent_window_instance
        while True:
            try:
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
                elif command == "is_running":
                    if transparent_window_instance:
                        is_running = transparent_window_instance.is_window_running()
                        output_conn.put(is_running)
            except Exception as e:
                print(f"Error in window controller: {e}")
                break

    def send_command(self, command):
        """Send a command to the transparent window."""
        if command in ["minimize", "maximize", "close", "stop", "show", "is_running"]:
            self.input_queue.put(command)

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

    def wait(self, timeout=5):
        timeout = time.time() + timeout
        while time.time() < timeout:
            self.send_command("is_running")
            try:
                if self.output_queue.get(timeout=0.5):
                    self.log("[WINDOW] Window is running correctly")
                    return True
            except queue.Empty:
                continue

        return False