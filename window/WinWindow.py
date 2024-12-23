import multiprocessing
import threading
import time
import wx
import atexit
from queue import Queue

from utils.Logging import Logger
from window.InterfaceWindow import AbstractHiddenWindow


class TransparentFullscreenWindow(wx.Frame):
    def __init__(self, parent, conn):
        atexit.register(wx.DisableAsserts)  # Disable asserts to avoid errors when closing the window

        super().__init__(parent, style=wx.STAY_ON_TOP | wx.FRAME_NO_TASKBAR | wx.NO_BORDER)
        self.conn = conn
        self.is_open = True
        self.is_fullscreen = False

        self.configure_window()
        self.Bind(wx.EVT_CLOSE, self.on_close)
        self.command_thread = threading.Thread(target=self.listen_for_commands, daemon=True)
        self.command_thread.start()

    def configure_window(self):
        self.SetSize(wx.GetDisplaySize())
        self.SetTransparent(1)  # Set full transparency
        self.ShowFullScreen(True, style=wx.FULLSCREEN_ALL)
        self.HideCursor()

    def HideCursor(self):
        cursor = wx.Cursor(wx.CURSOR_BLANK)
        self.SetCursor(cursor)

    def listen_for_commands(self):
        while self.is_open:
            command = self.conn.get()
            if command == 'close':
                wx.CallAfter(self.handle_close)
                return
            elif command == 'show':
                wx.CallAfter(self.maximize)
            elif command == 'stop':
                wx.CallAfter(self.handle_close)
                return
            elif command == 'minimize':
                wx.CallAfter(self.minimize)
            elif command == 'maximize':
                wx.CallAfter(self.maximize)
            elif command == 'toggle':
                wx.CallAfter(self.toggle)

    def handle_close(self, event=None):
        self.Destroy()

    def minimize(self):
        self.Iconize(True)
        self.is_fullscreen = False
        self.Show(False)
        self.SetCursor(wx.NullCursor)  # Reset the cursor to the default state

    def maximize(self):
        self.Iconize(False)
        self.Show(True)  # Show the window
        self.Raise()  # Bring the window to the front
        self.HideCursor()
        self.ShowFullScreen(True, style=wx.FULLSCREEN_ALL)
        self.is_fullscreen = True

    def toggle(self):
        if self.is_fullscreen:
            self.minimize()
        else:
            self.maximize()

    def on_close(self, event):
        self.is_open = False
        self.Destroy()


class MsgChannel:
    def __init__(self, conn):
        self.conn = conn

    def put(self, msg):
        if isinstance(self.conn, Queue):
            self.conn.put(msg)
        elif isinstance(self.conn, multiprocessing.Pipe):
            self.conn.send(msg)

    def get(self):
        if isinstance(self.conn, Queue):
            return self.conn.get()
        elif isinstance(self.conn, multiprocessing.Pipe):
            return self.conn.recv()


class HiddenWindow(AbstractHiddenWindow):
    def __init__(self):
        self.child_conn = Queue()
        self.process = None
        self.log = Logger.get_instance().log

    def start(self):
        self._start_window_app()

    def _start_window_app(self):
        """Start the window application and handle external commands."""
        self.process = threading.Thread(target=self.overlay_process, args=(self.child_conn,), daemon=True)
        self.process.start()

    @staticmethod
    def overlay_process(conn):
        app = wx.App()
        TransparentFullscreenWindow(None, conn)
        app.MainLoop()

    def send_command(self, command):
        """Send a command to the window process."""
        try:
            self.child_conn.put(command)
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
