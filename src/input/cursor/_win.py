"""
Logic to handle cursor visibility on Windows systems.
"""

from typing import Optional
import wx

from multiprocessing import Queue
from multiprocessing.connection import PipeConnection

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

        self.SetSizer(vbox)

        # Black background
        self.SetBackgroundColour(wx.Colour(0, 0, 0))


class CursorHandlerWindow(_base.CursorHandlerWindow):
    BORDER_OFFSET = 1
    WINDOW_SIZE = (200, 200)

    def __init__(
        self,
        command_queue: Queue,
        result_queue: Queue,
        mouse_conn: PipeConnection,
        debug: bool = False,
    ):
        super().__init__(
            command_queue,
            result_queue,
            mouse_conn,
            debug,
            size=self.WINDOW_SIZE,
            style=wx.STAY_ON_TOP | wx.FRAME_NO_TASKBAR | wx.NO_BORDER,
        )
        self.__size = self.WINDOW_SIZE
        # Panel principale
        self.panel = None

        # Windows-specific handle
        self.hwnd = None

        self._old_style = self.GetWindowStyle()
        # self.SetWindowStyle(
        #     self._old_style | wx.STAY_ON_TOP | wx.FRAME_NO_TASKBAR | wx.NO_BORDER  | wx.TRANSPARENT_WINDOW)
        self.SetTransparent(1)
        self._create()

    def MoveWindow(self, x: int = -1, y: int = -1) -> None:
        if x == -1 or y == -1:
            return

        # Denormalize coordinates
        screen_width, screen_height = wx.GetDisplaySize()
        x = int(x * screen_width)
        y = int(y * screen_height)

        try:
            self.Move(x, y)
        except Exception as e:
            print(f"Error moving window: {e}")

    def ForceOverlay(self):
        try:
            self.Iconize(False)
            self.AcceptsFocusRecursively()

            cursor_pos = wx.GetMousePosition()

            # Imposta dimensioni della finestra
            # width, height = 100, 100
            # self.SetSize(width, height)

            # Ottieni le dimensioni dello schermo dove si trova il cursore
            display_index = wx.Display.GetFromPoint(cursor_pos)
            if display_index == wx.NOT_FOUND:
                display_index = 0
            display = wx.Display(display_index)
            screen_rect = display.GetClientArea()

            # Offset minimo dai bordi (in pixel)
            offset = self.BORDER_OFFSET

            # Calcola la posizione per centrare la finestra sul cursore
            x = cursor_pos.x - self.__size[0] // 2
            y = cursor_pos.y -  self.__size[1] // 2

            # Applica i limiti considerando l'offset dai bordi
            x = max(screen_rect.x + offset - self.__size[0] // 2,
                    min(x, screen_rect.x + screen_rect.width - offset - self.__size[0] // 2))
            y = max(screen_rect.y + offset - self.__size[1] // 2,
                    min(y, screen_rect.y + screen_rect.height - offset - self.__size[1] // 2))

            self.Show(True)
            self.Move(x, y)
            self.Raise()
        except Exception as e:
            print(f"Error forcing overlay: {e}")

    def HideOverlay(self):
        try:
            self.Iconize(True)
            super().HideOverlay()
        except Exception as e:
            print(f"Error hiding overlay: {e}")

    def RestorePreviousApp(self):
        return

    def handle_cursor_visibility(self, visible: bool):
        """
        Handle cursor visibility for Windows.
        If visible is False, hide the cursor. If True, show the cursor.
        """
        if not visible:
            # Hide cursor using wx
            cursor = wx.Cursor(wx.CURSOR_BLANK)
            self.SetCursor(cursor)
        else:
            # Show cursor
            self.SetCursor(wx.NullCursor)


class CursorHandlerWorker(_base.CursorHandlerWorker):
    RESULT_POLL_TIMEOUT = 1  # sec
    DATA_POLL_TIMEOUT = 0.01

    def __init__(
        self,
        event_bus: EventBus,
        stream: Optional[StreamHandler] = None,
        debug: bool = False,
        window_class=CursorHandlerWindow,
    ):
        super().__init__(event_bus, stream, debug, window_class)
