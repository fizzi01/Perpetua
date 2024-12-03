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
    NSApplicationPresentationAutoHideMenuBar
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
    class OverlayFrame(wx.Frame):
        def __init__(self):
            self.previous_app = NSWorkspace.sharedWorkspace().frontmostApplication()
            self.previous_app_pid = self.previous_app.processIdentifier()
            self.fullscreen_windows = []

            # Ottieni la geometria dello schermo principale
            display = wx.Display(0)
            geometry = display.GetGeometry()
            display_x = geometry.GetX()
            display_y = geometry.GetY()
            display_width = geometry.GetWidth()
            display_height = geometry.GetHeight()

            # Inizializza il frame senza bordi e senza taskbar
            wx.Frame.__init__(
                self,
                None,
                title="",
                pos=(display_x, display_y),
                size=(display_width, display_height),
                style=wx.NO_BORDER | wx.FRAME_NO_TASKBAR
            )

            # Imposta l'opacità del frame
            self.SetTransparent(1)  # 50% di trasparenza

            # Imposta il colore di sfondo
            self.SetBackgroundColour(wx.Colour(0, 0, 0))

            # Nasconde il cursore usando le API di macOS
            NSCursor.hide()

            # Ottieni l'istanza di NSApplication
            NSApp = NSApplication.sharedApplication()

            # Imposta l'activation policy a Accessory per nascondere l'icona nel Dock
            NSApp.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

            # Mostra il frame
            self.Show()

            # Imposta il focus sulla finestra
            self.SetFocus()

            self.running = True
            self.pipe_thread = threading.Thread(target=self.listen_for_commands, args=(conn,))
            self.pipe_thread.daemon = True
            self.pipe_thread.start()

            # Flag per il thread di monitoraggio
            self.overlay_active = False
            self.monitor_thread = None

        def listen_for_commands(self, channel):
            while self.running:
                if channel.poll(0.1):  # Timeout per controllare self.running
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

        def HideOverlay(self):
            self.overlay_active = False  # Ferma il thread di monitoraggio
            if self.monitor_thread and self.monitor_thread.is_alive():
                self.monitor_thread.join()

            self.Hide()
            NSCursor.unhide()

            self.RestoreFullscreenApps()

            self.RestorePreviousApp()

        def ShowOverlay(self):
            self.HandleFullscreen()

            self.Show()
            NSCursor.hide()
            self.ForceOverlay()
            self.SetFocus()

            # Inizia il thread di monitoraggio
            self.overlay_active = True
            self.monitor_thread = threading.Thread(target=self.monitor_overlay)
            self.monitor_thread.daemon = True
            self.monitor_thread.start()

        def monitor_overlay(self):
            window_ptr = self.GetHandle()
            ns_view = objc.objc_object(c_void_p=window_ptr)
            ns_window = ns_view.window()

            while self.overlay_active:
                # Controlla se la finestra è la finestra chiave
                if not ns_window.isKeyWindow():
                    # Porta la finestra in primo piano e rendila la finestra chiave
                    wx.CallAfter(self.HandleFullscreen)
                    wx.CallAfter(self.Show)
                    wx.CallAfter(NSCursor.hide)
                    wx.CallAfter(self.ForceOverlay)
                    wx.CallAfter(self.SetFocus)
                time.sleep(0.1)  # Attendi 100 ms prima di controllare di nuovo

        def HandleFullscreen(self):
            self.previous_app = NSWorkspace.sharedWorkspace().frontmostApplication()
            self.previous_app_pid = self.previous_app.processIdentifier()

            # Ottieni le finestre dell'applicazione
            app_windows = self.get_application_windows(self.previous_app_pid)
            # Verifica se qualche finestra è in fullscreen
            for window in app_windows:
                is_fullscreen = self.is_window_fullscreen(window)
                if is_fullscreen:
                    # Esci dal fullscreen per questa finestra
                    self.set_window_fullscreen(window, False)
                    # Tieni traccia della finestra per ripristinare il fullscreen dopo
                    self.fullscreen_windows.append(window)

        def ForceOverlay(self):
            # Ottieni l'istanza di NSApplication
            NSApp = NSApplication.sharedApplication()
            # Imposta le opzioni di presentazione per nascondere il Dock e la barra dei menu
            NSApp.setPresentationOptions_(
                NSApplicationPresentationAutoHideDock | NSApplicationPresentationAutoHideMenuBar)
            # Attiva l'applicazione ignorando le altre
            NSApp.activateIgnoringOtherApps_(True)

            # Ottieni l'NSWindow associata alla finestra wxPython
            window_ptr = self.GetHandle()
            ns_view = objc.objc_object(c_void_p=window_ptr)
            ns_window = ns_view.window()

            # Imposta il livello della finestra per mantenerla sopra le altre
            ns_window.setLevel_(NSScreenSaverWindowLevel)

            # Imposta la finestra per apparire su tutti gli spazi
            ns_window.setCollectionBehavior_(NSWindowCollectionBehaviorCanJoinAllSpaces)

            ns_window.setIgnoresMouseEvents_(False)

            # Rende la finestra la finestra chiave e la porta in primo piano
            ns_window.makeKeyAndOrderFront_(None)

        def RestorePreviousApp(self):
            if self.previous_app:
                self.previous_app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
            self.previous_app = None
            self.previous_app_pid = None

        def RestoreFullscreenApps(self):
            for window in self.fullscreen_windows:
                self.set_window_fullscreen(window, True)
            self.fullscreen_windows = []

        @staticmethod
        def get_application_windows(pid):
            app_element = AXUIElementCreateApplication(pid)
            error, result = AXUIElementCopyAttributeValue(app_element, kAXWindowsAttribute, None)
            if error != 0:
                print("Errore nell'ottenere le finestre dell'applicazione.")
                return []
            return result

        @staticmethod
        def is_window_fullscreen(window):
            error, fullscreen_value = AXUIElementCopyAttributeValue(window, kAXFullScreenAttribute, None)
            if error == 0:
                return bool(fullscreen_value)
            return False

        @staticmethod
        def set_window_fullscreen(window, fullscreen):
            error = AXUIElementSetAttributeValue(window, kAXFullScreenAttribute, fullscreen)
            if error != 0:
                print("Errore nell'impostare lo stato di fullscreen della finestra.")

    app = wx.App()
    OverlayFrame()
    app.MainLoop()
    conn.close()


class HiddenWindow(AbstractHiddenWindow):
    def __init__(self):
        self.parent_conn, self.child_conn = multiprocessing.Pipe()

        self.process = None
        self.log = Logger.get_instance().log

        self._start_window_app(self.parent_conn, self.child_conn)

    def _start_window_app(self, input_conn, output_conn):
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
