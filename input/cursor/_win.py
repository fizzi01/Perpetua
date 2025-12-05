"""
Logic to handle cursor visibility on Windows systems.
"""
from queue import Empty
from typing import Optional
from ctypes import windll, c_int, byref, Structure, POINTER, c_uint

import wx

from multiprocessing import Queue
from multiprocessing.connection import Connection

# Windows API imports
import ctypes
from ctypes import wintypes

from event.EventBus import EventBus
from input.cursor._base import BaseCursorHandlerWindow, BaseCursorHandlerWorker
from network.stream.GenericStream import StreamHandler


# Windows API constants
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
SW_HIDE = 0
SW_SHOW = 5
GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000

# Load Windows DLLs
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32


class POINT(Structure):
    _fields_ = [("x", c_int), ("y", c_int)]


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

        self.SetSizer(vbox)

        # Black background
        self.SetBackgroundColour(wx.Colour(10, 10, 10))


class CursorHandlerWindow(BaseCursorHandlerWindow):

    def __init__(self, command_queue: Queue, result_queue: Queue, mouse_conn: Connection, debug: bool = False):
        super().__init__(command_queue, result_queue, mouse_conn, debug)

        # Panel principale
        self.panel = DebugOverlayPanel(self)

        # Windows-specific handle
        self.hwnd = None

        self._create()

    def _process_commands(self):
        """Processa i comandi dalla queue"""
        try:
            while self._running:
                try:
                    command = self.command_queue.get(timeout=0.2)
                    cmd_type = command.get('type')

                    if cmd_type == 'enable_capture':
                        wx.CallAfter(self.enable_mouse_capture)
                        self.result_queue.put({'type': 'capture_enabled', 'success': True})

                    elif cmd_type == 'disable_capture':
                        wx.CallAfter(self.disable_mouse_capture)
                        self.result_queue.put({'type': 'capture_disabled', 'success': True})
                    elif cmd_type == 'get_stats':
                        self.result_queue.put({
                            'type': 'stats',
                            'is_captured': self.mouse_captured,
                        })

                    elif cmd_type == 'set_message':
                        message = command.get('message', '')
                        self.panel.info_text.SetLabel(message)
                        self.result_queue.put({'type': 'message_set', 'success': True})

                    elif cmd_type == 'quit':
                        self._running = False
                        self.Close()
                except Empty:
                    continue
        except Exception as e:
            print(f"Error processing commands: {e}")

    def _get_hwnd(self):
        """Get the Windows HWND handle for this window"""
        if self.hwnd is None:
            self.hwnd = self.GetHandle()
        return self.hwnd

    def ForceOverlay(self):
        try:
            super().ForceOverlay()

            # Store the previously active window
            self.previous_app = user32.GetForegroundWindow()

            hwnd = self._get_hwnd()

            # Set window as topmost
            user32.SetWindowPos(
                hwnd,
                HWND_TOPMOST,
                0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE
            )

            # Set extended window style to make it always on top
            ex_style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex_style | WS_EX_TOPMOST)

            # Force window to foreground
            user32.SetForegroundWindow(hwnd)
            user32.SetFocus(hwnd)

        except Exception as e:
            print(f"Error forcing overlay: {e}")

    def HideOverlay(self):
        try:
            hwnd = self._get_hwnd()

            # Remove topmost flag
            ex_style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex_style & ~WS_EX_TOPMOST)

            user32.SetWindowPos(
                hwnd,
                HWND_NOTOPMOST,
                0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE
            )

            super().HideOverlay()
        except Exception as e:
            print(f"Error hiding overlay: {e}")

    def RestorePreviousApp(self):
        try:
            if self.previous_app:
                user32.SetForegroundWindow(self.previous_app)
            self.previous_app = None
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
        Handle cursor visibility for Windows.
        If visible is False, hide the cursor. If True, show the cursor.
        """
        if not visible:
            # Hide cursor using wx
            cursor = wx.Cursor(wx.CURSOR_BLANK)
            self.SetCursor(cursor)

            # Also hide using Windows API for more reliability
            while user32.ShowCursor(False) >= 0:
                pass
        else:
            # Show cursor
            self.SetCursor(wx.NullCursor)

            # Show using Windows API
            while user32.ShowCursor(True) < 0:
                pass

    def reset_mouse_position(self):
        """Reset mouse position to center using Windows API"""
        if self.mouse_captured and self.center_pos:
            # Use Windows API for more reliable positioning
            user32.SetCursorPos(self.center_pos[0], self.center_pos[1])

            # Also use wx method as backup
            client_center = self.ScreenToClient(self.center_pos)
            self.WarpPointer(client_center.x, client_center.y)

    def on_mouse_move(self, event):
        if not self.mouse_captured:
            event.Skip()
            return

        # Get current mouse position
        point = POINT()
        user32.GetCursorPos(byref(point))

        # Calculate delta from center
        delta_x = point.x - self.center_pos[0]
        delta_y = point.y - self.center_pos[1]

        # Process only if there's movement
        if delta_x != 0 or delta_y != 0:
            if self.mouse_conn:
                try:
                    self.mouse_conn.send({'dx': delta_x, 'dy': delta_y})
                except:
                    pass

            if self._debug:
                self.panel.delta_text.SetLabel(f"Delta X: {delta_x}, Delta Y: {delta_y}")

            # Reset position to center
            self.reset_mouse_position()

        event.Skip()

    def update_ui(self, panel_obj, data, call):
        try:
            call(data)
        except Exception as e:
            pass


class CursorHandlerWorker(BaseCursorHandlerWorker):
    def __init__(self, event_bus: EventBus, stream: Optional[StreamHandler] = None, debug: bool = False,
                 window_class=CursorHandlerWindow):
        super().__init__(event_bus, stream, debug, window_class)

