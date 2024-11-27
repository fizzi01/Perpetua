import multiprocessing
import threading
import time

import wx
from AppKit import (
    NSCursor,
    NSApplication,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSScreenSaverWindowLevel,
    NSApplicationActivationPolicyAccessory,
    NSWorkspace,
    NSApplicationActivateIgnoringOtherApps
)
import objc

from window.InterfaceWindow import AbstractHiddenWindow
from utils.Logging import Logger

def overlay_process(conn):
    class OverlayFrame(wx.Frame):
        def __init__(self):
            self.previous_app = NSWorkspace.sharedWorkspace().frontmostApplication()
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

        def listen_for_commands(self, conn):
            while self.running:
                if conn.poll(0.1):  # Timeout per controllare self.running
                    try:
                        command = conn.recv()
                        if command == 'minimize':
                            wx.CallAfter(self.HideOverlay)
                        elif command == 'maximize':
                            wx.CallAfter(self.ShowOverlay)
                        elif command == 'stop':
                            wx.CallAfter(self.Close)
                            conn.close()
                        elif command == 'is_running':
                            conn.send(True)
                    except EOFError:
                        break

        def HideOverlay(self):
            self.Hide()
            NSCursor.unhide()
            if self.previous_app:
                self.previous_app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)

        def ShowOverlay(self):
            self.previous_app = NSWorkspace.sharedWorkspace().frontmostApplication()
            self.Show()
            NSCursor.hide()
            self.ForceOverlay()
            self.SetFocus()

        def ForceOverlay(self):
            # Attiva l'applicazione e la porta in primo piano
            NSApp = NSApplication.sharedApplication()
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

    app = wx.App()
    OverlayFrame()
    app.MainLoop()
    conn.close()


class HiddenWindow(AbstractHiddenWindow):
    def __init__(self, root=None):
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