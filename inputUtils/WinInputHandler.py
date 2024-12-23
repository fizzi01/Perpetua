import math
import threading
import time
import urllib
from urllib import parse
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

# System libraries
import win32clipboard
import win32con
import win32gui
import win32process
from comtypes import client
import psutil
import os

# Input handling libraries
import pyperclip
from pynput import mouse
from pynput.keyboard import Key, KeyCode, Listener as KeyboardListener, Controller as KeyboardController
from pynput.mouse import Button, Controller as MouseController, Listener as MouseListener

from client.state.ClientState import HiddleState
from utils.Interfaces import (
    IServerContext, IMessageService, IHandler, IEventBus, IMouseController,
    IClipboardController, IFileTransferContext, IFileTransferService, IClientContext,
    IScreenContext, IKeyboardController, IControllerContext, IMouseListener
)
from utils.Logging import Logger
from utils.net.netData import format_command


class ServerMouseController(IMouseController):
    """
    Wrapper class for the mouse controller
    This class is used to control the mouse on the server side
    :param context: The server context
    """

    def __init__(self, context: IServerContext):
        self.context = context
        self.logger = Logger.log
        self.mouse_controller = MouseController()

    def process_mouse_command(self, x: int | float, y: int | float, mouse_action: str, is_pressed: bool):
        pass

    def get_current_position(self):
        return self.mouse_controller.position

    def set_position(self, x, y):
        self.mouse_controller.position = (x, y)

    def move(self, dx, dy):
        self.mouse_controller.move(dx, dy)

    def start(self):
        pass

    def stop(self):
        pass


class ServerClipboardController(IClipboardController):
    """
        Wrapper class for the clipboard controller
        This class is used to control the clipboard on the server side
        :param context: The server context
    """

    def __init__(self, context: IServerContext):
        self.context = context
        self.logger = Logger.get_instance().log

    def get_clipboard_data(self) -> str:
        return pyperclip.paste()

    def set_clipboard_data(self, data: str) -> None:
        pyperclip.copy(data)


class ServerMouseListener(IMouseListener):
    """
    Listener del mouse lato Server in ambiente Windows.
    Adattato alla stessa interfaccia di OSXInputHandler.ServerMouseListener
    """
    IGNORE_NEXT_MOVE_EVENT = 0.001
    MAX_DXDY_THRESHOLD = 100
    SCREEN_CHANGE_DELAY = 0.001
    EMULATION_STOP_DELAY = 0.1

    def __init__(
            self,
            context: IServerContext | IControllerContext,
            message_service: IMessageService,
            event_bus: IEventBus,
            screen_width: int,
            screen_height: int,
            screen_threshold: int = 5
    ):
        self.context = context
        self.event_bus = event_bus

        self.screen_width = screen_width
        self.screen_height = screen_height
        self.screen_threshold = screen_threshold

        self.logger = Logger.log
        self.send = message_service.send_mouse

        self.last_x = None
        self.last_y = None
        self.x_print = 0
        self.y_print = 0
        self.mouse_controller: Optional[IMouseController] = None
        self.buttons_pressed = set()
        self.stop_emulation = False
        self.stop_emulation_timeout = 0
        self.screen_change_in_progress = False
        self.screen_change_timeout = 0
        self.ignore_move_events_until = 0

        self.to_warp = threading.Event()
        self.stop_warp = threading.Event()

        # In Windows, l’equivalente di darwin_intercept è win32_event_filter
        self._listener = MouseListener(
            on_move=self.on_move,
            on_scroll=self.on_scroll,
            on_click=self.on_click,
            win32_event_filter=self.mouse_suppress_filter
        )

    def get_position(self):
        return self.x_print, self.y_print

    def get_listener(self):
        return self._listener

    def start(self):
        # Get the mouse controller from the context
        if isinstance(self.context, IControllerContext):
            self.mouse_controller = self.context.mouse_controller
        else:  # In case of wrong context, cancel the start
            raise ValueError("Mouse controller not found in the context")

        self._listener.start()
        threading.Thread(target=self.warp_cursor_to_center, daemon=True).start()
        self.stop_warp.clear()
        self.to_warp.clear()

    def stop(self):
        if self.is_alive():
            self._listener.stop()
        self.stop_warp.set()
        self.to_warp.set()

    def is_alive(self):
        return self._listener.is_alive()

    def mouse_suppress_filter(self, msg, data):
        """
        Intercetta e sopprime gli eventi di mouse se siamo su uno screen remoto.
        """
        screen = self.context.get_active_screen()
        if screen:
            # Se c'è uno screen attivo, sopprime alcuni eventi di click/scroll
            # in modo simile a come avviene su macOS (darwin_intercept).
            # msg = 513/514 -> left down/up
            # msg = 516/517 -> right down/up
            # msg = 519/520 -> middle down/up
            # msg = 522/523 -> scroll
            if msg in (513, 514, 516, 517, 519, 520, 522, 523):
                self._listener._suppress = True
            else:
                self._listener._suppress = False
        else:
            self._listener._suppress = False

        return True

    def warp_cursor_to_center(self):
        while not self.stop_warp.is_set():
            try:
                if self.to_warp.is_set() and self.context.is_transition_in_progress():
                    current_time = time.time()

                    if self.stop_emulation and current_time <= self.stop_emulation_timeout:
                        return

                    if self.screen_change_in_progress and current_time <= self.screen_change_timeout:
                        return

                    self.ignore_move_events_until = time.time() + self.IGNORE_NEXT_MOVE_EVENT
                    center_x = self.screen_width // 2
                    center_y = self.screen_height // 2

                    if not self.stop_emulation and self.to_warp.is_set():
                        self.mouse_controller.set_position(center_x, center_y)
                        self.last_x, self.last_y = self.mouse_controller.get_current_position()
                        self.to_warp.clear()
            except TypeError:
                pass
            except AttributeError:
                pass

            time.sleep(0.01)

    def on_move(self, x, y):
        self.to_warp.clear()
        current_time = time.time()

        # Se la simulazione era fermata, verifica se il timeout è passato
        if self.stop_emulation and current_time > self.stop_emulation_timeout and len(self.buttons_pressed) == 0:
            self.stop_emulation = False

        # Ignora i movimenti se siamo ancora nel tempo di "ignore"
        if not self.stop_emulation and self.ignore_move_events_until > current_time:
            self.last_x = x
            self.last_y = y
            return True

        # Check screen change in corso
        if self.screen_change_in_progress and current_time > self.screen_change_timeout:
            self.screen_change_in_progress = False

        dx = 0
        dy = 0
        if self.last_x is not None and self.last_y is not None:
            dx = x - self.last_x
            dy = y - self.last_y

        # Ignora movimenti anomali
        if abs(dx) > self.MAX_DXDY_THRESHOLD or abs(dy) > self.MAX_DXDY_THRESHOLD:
            self.last_x = x
            self.last_y = y
            return True

        self.last_x = x
        self.last_y = y

        at_right_edge = x >= self.screen_width - 1
        at_left_edge = x <= 0
        at_bottom_edge = y >= self.screen_height - 1
        at_top_edge = y <= 0

        screen = self.context.get_active_screen()
        client = self.context.get_client(screen)
        is_transmitting = self.context.is_transition_in_progress()

        # Se c'è uno screen attivo e siamo in trasmissione
        if screen and client and is_transmitting:
            if not self.buttons_pressed and not self.stop_emulation:
                self.x_print += dx
                self.y_print += dy
                # Clipping
                self.x_print = max(0, min(self.x_print, self.screen_width))
                self.y_print = max(0, min(self.y_print, self.screen_height))

                self.send(
                    screen,
                    format_command(
                        f"mouse position {self.x_print / self.screen_width} {self.y_print / self.screen_height}"
                    )
                )
                self.to_warp.set()
            elif self.stop_emulation or self.buttons_pressed:
                normalized_x = x / self.screen_width
                normalized_y = y / self.screen_height
                self.send(screen, format_command(f"mouse position {normalized_x} {normalized_y}"))

        # Se non siamo in emulazione e non stiamo cliccando, verifichiamo se attraversiamo un bordo
        elif not self.buttons_pressed and not self.screen_change_in_progress:
            if at_right_edge:
                self.stop_emulation = False
                self.screen_change_in_progress = True
                self.screen_change_timeout = time.time() + self.SCREEN_CHANGE_DELAY
                self.event_bus.publish(IEventBus.SCREEN_CHANGE_EVENT, "right")

                normalized_x = 0
                normalized_y = y / self.screen_height
                self.send("right", format_command(f"mouse position {normalized_x} {normalized_y}"))
                self.x_print = normalized_x * self.screen_width
                self.y_print = normalized_y * self.screen_height

            elif at_left_edge:
                self.stop_emulation = False
                self.screen_change_in_progress = True
                self.screen_change_timeout = time.time() + self.SCREEN_CHANGE_DELAY
                self.event_bus.publish(IEventBus.SCREEN_CHANGE_EVENT, "left")

                normalized_x = 1
                normalized_y = y / self.screen_height
                self.send("left", format_command(f"mouse position {normalized_x} {normalized_y}"))
                self.x_print = normalized_x * self.screen_width
                self.y_print = normalized_y * self.screen_height

            elif at_bottom_edge:
                self.stop_emulation = False
                self.screen_change_in_progress = True
                self.screen_change_timeout = time.time() + self.SCREEN_CHANGE_DELAY
                self.event_bus.publish(IEventBus.SCREEN_CHANGE_EVENT, "down")

                normalized_x = x / self.screen_width
                normalized_y = 0
                self.send("down", format_command(f"mouse position {normalized_x} {normalized_y}"))
                self.x_print = normalized_x * self.screen_width
                self.y_print = normalized_y * self.screen_height

            elif at_top_edge:
                self.stop_emulation = False
                self.screen_change_in_progress = True
                self.screen_change_timeout = time.time() + self.SCREEN_CHANGE_DELAY
                self.event_bus.publish(IEventBus.SCREEN_CHANGE_EVENT, "up")

                normalized_x = x / self.screen_width
                normalized_y = 1
                self.send("up", format_command(f"mouse position {normalized_x} {normalized_y}"))
                self.x_print = normalized_x * self.screen_width
                self.y_print = normalized_y * self.screen_height

        if self.stop_emulation:
            self.x_print, self.y_print = x, y

        return True

    def on_click(self, x, y, button, pressed):
        screen = self.context.get_active_screen()
        client = self.context.get_client(screen)
        is_transmitting = self.context.is_transition_in_progress()

        # Se è un left click in trasmissione
        if button == mouse.Button.left and pressed and screen and client and is_transmitting:
            self.buttons_pressed.add(button)
            if not self.stop_emulation:
                self.mouse_controller.set_position(self.x_print, self.y_print)

            self.stop_emulation = True
            self.stop_emulation_timeout = time.time() + self.EMULATION_STOP_DELAY
        else:
            self.buttons_pressed.discard(button)
            current_time = time.time()
            if self.stop_emulation and current_time > self.stop_emulation_timeout:
                self.stop_emulation = False

        if button == mouse.Button.left:
            if screen and client and pressed:
                self.send(screen, format_command(f"mouse click {x} {y} true"))
            elif screen and client and not pressed:
                self.send(screen, format_command(f"mouse click {x} {y} false"))
        elif button == mouse.Button.right:
            if screen and client and pressed:
                self.send(screen, format_command(f"mouse right_click {x} {y}"))
        elif button == mouse.Button.middle:
            if screen and client and pressed:
                self.send(screen, format_command(f"mouse middle_click {x} {y}"))

        return True

    def on_scroll(self, x, y, dx, dy):
        screen = self.context.get_active_screen()
        client = self.context.get_client(screen)
        if screen and client:
            self.send(screen, format_command(f"mouse scroll {dx} {dy}"))
        return True

    def __str__(self):
        return "ServerMouseListener"


class ServerKeyboardListener(IHandler):
    """
    Listener della tastiera lato Server in ambiente Windows.
    Adattato per avere la stessa interfaccia di OSXInputHandler.ServerKeyboardListener
    """

    def __init__(
            self,
            context: IServerContext | IFileTransferContext,
            message_service: IMessageService,
            event_bus: IEventBus
    ):
        self.context = context
        self.event_bus = event_bus
        self.send = message_service.send_keyboard
        self.logger = Logger.get_instance().log

        self.file_transfer_handler: IFileTransferService | None = None
        self.command_pressed = False

        # In Windows l’equivalente di darwin_intercept è win32_event_filter
        self._listener = KeyboardListener(
            on_press=self.on_press,
            on_release=self.on_release,
            win32_event_filter=self.keyboard_suppress_filter
        )

    def get_listener(self):
        return self._listener

    def start(self):
        # Se stiamo usando contesto di file transfer, recuperiamo l’handler
        if isinstance(self.context, IFileTransferContext):
            self.file_transfer_handler = self.context.file_transfer_service

        self._listener.start()

    def stop(self):
        if self.is_alive():
            self._listener.stop()

    def is_alive(self):
        return self._listener.is_alive()

    @staticmethod
    def get_current_clicked_directory():
        try:
            # Ottieni l'handle della finestra attiva
            hwnd = win32gui.GetForegroundWindow()

            # Ottieni il processo associato
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            process = psutil.Process(pid)

            # Verifica se il processo è Esplora Risorse
            if "explorer.exe" in process.name().lower():
                # Verifica se la finestra attiva è il Desktop
                # Desktop ha tipicamente titolo vuoto o nullo
                window_text = win32gui.GetWindowText(hwnd).strip()
                if window_text == 'Program Manager':
                    # Restituisce il percorso del Desktop
                    desktop_path = os.path.join(os.environ["USERPROFILE"], "Desktop")
                    return desktop_path

                shell = client.CreateObject("Shell.Application")
                windows = shell.Windows()

                for window in windows:
                    # Confronta l'handle della finestra attiva
                    if int(hwnd) == int(window.HWND):
                        # Ottieni il percorso della directory attiva
                        directory = window.LocationURL
                        if directory.startswith("file:///"):
                            # Convert to a readable path
                            directory = urllib.parse.unquote(directory[8:].replace("/", "\\"))
                        return directory

            # Se il processo non è Esplora Risorse
            return None
        except Exception as e:
            return None

    def keyboard_suppress_filter(self, msg, data):
        screen = self.context.get_active_screen()
        if screen:
            self._listener._suppress = True
        else:
            self._listener._suppress = False

    def on_press(self, key: Key | KeyCode | None):
        screen = self.context.get_active_screen()
        clients = self.context.get_client(screen)

        if isinstance(key, Key):
            data = key.name
        else:
            data = key.char

        # Riconosciamo ctrl come "command_pressed"
        if key in [Key.ctrl, Key.ctrl_l]:
            self.command_pressed = True
        # Se si preme ctrl+v
        elif data == "\x16" and self.command_pressed:  # \x16 = 'V'
            current_dir = self.get_current_clicked_directory()
            if current_dir and self.file_transfer_handler:
                self.file_transfer_handler.handle_file_paste(current_dir)

        if screen and clients:
            self.send(screen, format_command(f"keyboard press {data}"))

    def on_release(self, key: Key | KeyCode | None):
        screen = self.context.get_active_screen()
        clients = self.context.get_client(screen)

        if isinstance(key, Key):
            data = key.name
        else:
            data = key.char

        if key in [Key.ctrl, Key.ctrl_l]:
            self.command_pressed = False

        if screen and clients:
            self.send(screen, format_command(f"keyboard release {data}"))

    def __str__(self):
        return "ServerKeyboardListener"


class ServerClipboardListener(IHandler):

    def __init__(
            self,
            context: IServerContext | IControllerContext | IFileTransferContext,
            message_service: IMessageService,
            event_bus: IEventBus
    ):
        self.context = context
        self.event_bus = event_bus
        self.send = message_service.send_clipboard

        self._stop_event = threading.Event()
        self._thread = None

        self.logger = Logger.log

        self.clipboard_controller: Optional[IClipboardController] = None
        self.last_clipboard_content: Optional[str] = None
        self.last_clipboard_files = None

        self.file_transfer_handler: Optional[IFileTransferService] = None

    def start(self):
        if isinstance(self.context, IFileTransferContext):
            self.file_transfer_handler = self.context.file_transfer_service
        else:
            raise ValueError("File transfer handler not found in the context")

        if isinstance(self.context, IControllerContext):
            clip_ctrl = self.context.clipboard_controller
            if clip_ctrl:
                self.clipboard_controller = clip_ctrl
                self.last_clipboard_content = clip_ctrl.get_clipboard_data()
            else:
                raise ValueError("Clipboard controller not found in the context")

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run)
        self._thread.start()

    def stop(self):
        self._stop_event.set()

    def is_alive(self):
        return self._thread.is_alive() if self._thread else False

    @staticmethod
    def get_clipboard_files():
        win32clipboard.OpenClipboard()
        try:
            # Controlla se il contenuto è di tipo CF_H ROP (file)
            if win32clipboard.IsClipboardFormatAvailable(win32con.CF_HDROP):
                # Ottieni i percorsi dei file copiati
                file_paths = win32clipboard.GetClipboardData(win32con.CF_HDROP)
                # Filter out directories and return only the last copied file
                files = [path for path in file_paths if os.path.isfile(path)]
                return files
            else:
                return None
        finally:
            # Chiudi la clipboard
            win32clipboard.CloseClipboard()

    @staticmethod
    def get_file_info(file_path: str):
        if os.path.isfile(file_path):
            return {
                'file_name': os.path.basename(file_path),
                'file_size': os.path.getsize(file_path),
                'file_path': file_path
            }
        return None

    def _run(self):
        while not self._stop_event.is_set():
            try:
                current_clipboard_content = self.clipboard_controller.get_clipboard_data()
                current_clipboard_files = self.get_clipboard_files()

                # Se ci sono file nuovi in clipboard
                if current_clipboard_files and current_clipboard_files != self.last_clipboard_files:
                    # Takes the last file in the list
                    file_info = self.get_file_info(current_clipboard_files[-1])
                    if file_info:

                        file_name = file_info.get("file_name", "")
                        file_size = file_info.get("file_size", 0)
                        file_path = file_info.get("file_path", "")
                        if self.file_transfer_handler:
                            self.file_transfer_handler.handle_file_copy(file_name=file_name,
                                                                        file_size=file_size,
                                                                        file_path=file_path,
                                                                        local_owner=self.file_transfer_handler.LOCAL_SERVER_OWNERSHIP)

                        self.last_clipboard_files = current_clipboard_files

                # Se è cambiato il testo in clipboard
                elif current_clipboard_content != self.last_clipboard_content:
                    self.send("all", format_command("clipboard ") + current_clipboard_content)
                    self.last_clipboard_content = current_clipboard_content

                time.sleep(0.5)
            except Exception as e:
                # Reset the clipboard content if an error occurs
                self.last_clipboard_content = self.clipboard_controller.get_clipboard_data()
                self.logger(f"[CLIPBOARD] {e}", Logger.ERROR)

    def __str__(self):
        return "ServerClipboardListener"


class ClientClipboardListener(IHandler):
    """
    Listener della clipboard lato Client in ambiente Windows.
    Adattato come in OSXInputHandler.ClientClipboardListener
    """

    def __init__(
            self,
            context: IClientContext | IFileTransferContext,
            message_service: IMessageService,
            event_bus: IEventBus
    ):
        self.context = context
        self.event_bus = event_bus
        self.send = message_service.send_clipboard

        self._stop_event = threading.Event()
        self._thread = None

        self.logger = Logger.log

        self.clipboard_controller: Optional[IClipboardController] = None
        self.last_clipboard_content: Optional[str] = None
        self.last_clipboard_files = None

        self.file_transfer_handler: IFileTransferService | None = None

    def start(self):
        if isinstance(self.context, IFileTransferContext):
            self.file_transfer_handler = self.context.file_transfer_service
        else:
            raise ValueError("File transfer handler not found in the context")

        if isinstance(self.context, IControllerContext):
            clip_ctrl = self.context.clipboard_controller
            if clip_ctrl:
                self.clipboard_controller = clip_ctrl
                self.last_clipboard_content = clip_ctrl.get_clipboard_data()
            else:
                raise ValueError("Clipboard controller not found in the context")

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run)
        self._thread.start()

    def stop(self):
        self._stop_event.set()

    def is_alive(self):
        return self._thread.is_alive() if self._thread else False

    @staticmethod
    def get_clipboard_files():
        win32clipboard.OpenClipboard()
        try:
            # Controlla se il contenuto è di tipo CF_H ROP (file)
            if win32clipboard.IsClipboardFormatAvailable(win32con.CF_HDROP):
                # Ottieni i percorsi dei file copiati
                file_paths = win32clipboard.GetClipboardData(win32con.CF_HDROP)
                # Filter out directories and return only the last copied file
                files = [path for path in file_paths if os.path.isfile(path)]
                return files
            else:
                return None
        finally:
            # Chiudi la clipboard
            win32clipboard.CloseClipboard()

    @staticmethod
    def get_file_info(file_path: str):
        if os.path.isfile(file_path):
            return {
                'file_name': os.path.basename(file_path),
                'file_size': os.path.getsize(file_path),
                'file_path': file_path
            }
        return None

    def _run(self):
        while not self._stop_event.is_set():
            try:
                current_clipboard_content = self.clipboard_controller.get_clipboard_data()
                current_clipboard_files = self.get_clipboard_files()

                if current_clipboard_files and current_clipboard_files != self.last_clipboard_files:
                    file_info = self.get_file_info(current_clipboard_files[-1])
                    if file_info:

                        file_name = file_info.get("file_name", "")
                        file_size = file_info.get("file_size", 0)
                        file_path = file_info.get("file_path", "")
                        if self.file_transfer_handler:
                            self.file_transfer_handler.handle_file_copy(file_name=file_name,
                                                                        file_size=file_size,
                                                                        file_path=file_path,
                                                                        local_owner=self.file_transfer_handler.LOCAL_OWNERSHIP)

                        self.last_clipboard_files = current_clipboard_files
                elif current_clipboard_content != self.last_clipboard_content:
                    self.send(None, format_command("clipboard ") + current_clipboard_content)
                    self.last_clipboard_content = current_clipboard_content

                time.sleep(0.5)
            except Exception as e:
                self.logger(f"[CLIPBOARD] {e}", Logger.ERROR)

    def __str__(self):
        return "ClientClipboardListener"


class ClientMouseListener(IHandler):

    def __init__(
            self,
            context: IClientContext,
            message_service: IMessageService,
            event_bus: IEventBus,
            screen_width: int,
            screen_height: int,
            screen_threshold: int = 5
    ):
        self.context = context
        self.event_bus = event_bus
        self.send = message_service.send_mouse

        self.screen_width = screen_width
        self.screen_height = screen_height
        self.threshold = screen_threshold

        self._listener = MouseListener(on_move=self.handle_mouse)
        self.logger = Logger.log

    def start(self):
        self._listener.start()

    def stop(self):
        if self.is_alive():
            self._listener.stop()

    def is_alive(self):
        return self._listener.is_alive()

    def handle_mouse(self, x, y):

        if self.context.get_state():
            if x <= self.threshold:
                self.send(None, format_command(f"return left {y / self.screen_height}"))
                self.context.set_state(HiddleState())
            elif x >= self.screen_width - self.threshold:
                self.send(None, format_command(f"return right {y / self.screen_height}"))
                self.context.set_state(HiddleState())
            elif y <= self.threshold:
                self.send(None, format_command(f"return up {x / self.screen_width}"))
                self.context.set_state(HiddleState())
            elif y >= self.screen_height - self.threshold:
                self.send(None, format_command(f"return down {x / self.screen_width}"))
                self.context.set_state(HiddleState())

        return True

    def __str__(self):
        return "ClientMouseListener"


class ClientKeyboardListener(IHandler):
    """
    Listener della tastiera lato Client in ambiente Windows.
    Adattato come in OSXInputHandler.ClientKeyboardListener
    """

    def __init__(
            self,
            context: IClientContext | IFileTransferContext,
            message_service: IMessageService,
            event_bus: IEventBus
    ):
        self.context = context
        self.send = message_service.send_keyboard
        self.event_bus = event_bus

        self.file_transfer_handler: IFileTransferService | None = None
        self.command_pressed = False

        self._listener = KeyboardListener(on_press=self.on_press, on_release=self.on_release)
        self.logger = Logger.log

    def start(self):
        if isinstance(self.context, IFileTransferContext):
            self.file_transfer_handler = self.context.file_transfer_service

        self._listener.start()

    def stop(self):
        if self.is_alive():
            self._listener.stop()

    def is_alive(self):
        return self._listener.is_alive()

    @staticmethod
    def get_current_clicked_directory():
        """
        Controlla se la finestra attiva è Explorer
        e preleva la cartella selezionata.
        """
        try:
            # Ottieni l'handle della finestra attiva
            hwnd = win32gui.GetForegroundWindow()

            # Ottieni il processo associato
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            process = psutil.Process(pid)

            # Verifica se il processo è Esplora Risorse
            if "explorer.exe" in process.name().lower():
                # Verifica se la finestra attiva è il Desktop
                # Desktop ha tipicamente titolo vuoto o nullo
                window_text = win32gui.GetWindowText(hwnd).strip()
                if window_text == 'Program Manager' or window_text == '':
                    # Restituisce il percorso del Desktop
                    desktop_path = os.path.join(os.environ["USERPROFILE"], "Desktop")
                    # Clean the path
                    return desktop_path

                shell = client.CreateObject("Shell.Application")
                windows = shell.Windows()

                for window in windows:
                    # Compare the handle of the active window
                    if int(hwnd) == int(window.HWND):
                        # Get the path of the active directory
                        directory = window.LocationURL
                        if directory.startswith("file:///"):
                            # Convert URL format to Windows path
                            directory = urllib.parse.unquote(directory[8:].replace("/", "\\"))
                        return directory

            # Se il processo non è Esplora Risorse
            return None

        except AttributeError:
            return None
        except TypeError:
            return None
        except Exception:
            return None

    def on_press(self, key: Key | KeyCode | None):
        if isinstance(key, Key):
            data = key.name
        else:
            data = key.char

        # Riconosciamo ctrl come "command_pressed"
        if key in [Key.ctrl, Key.ctrl_l]:
            self.command_pressed = True
        # Se ctrl+v
        elif data == "\x16" and self.command_pressed:  # \x16 = 'V'
            current_dir = self.get_current_clicked_directory()
            if current_dir:
                self.file_transfer_handler.handle_file_paste(current_dir)

    def on_release(self, key: Key | KeyCode | None):
        if key in [Key.ctrl, Key.ctrl_l]:
            self.command_pressed = False


class ClientKeyboardController(IKeyboardController):

    def __init__(self, context: IClientContext):
        self.context = context
        self.pressed_keys = set()
        self.key_filter = {
            "option": "alt",
            "option_r": "alt_gr",
            "alt_r": "alt_gr",
        }
        self.special_keys = ["{", "}", "[", "]", "@", "#", "~", "«", "»", "€", "£", "“", "”", "’", "‘", "´", "`", "^",
                             "¨", "‹", "÷", "≠", "¡", "ˆ", "¥"]
        self.controller = KeyboardController()
        self.caps_lock_state = False

    def start(self):
        pass

    def stop(self):
        pass

    def data_filter(self, key_data):
        return self.key_filter.get(key_data, key_data)

    @staticmethod
    def get_key(key_data: str):
        try:
            key = Key[key_data]
            return key
        except KeyError:
            return key_data

    def is_special_key(self, key_data: str | Key):
        if isinstance(key_data, Key):
            return Key.name in self.special_keys
        else:
            return key_data in self.special_keys

    def process_key_command(self, key_data, key_action):
        key_data = self.data_filter(key_data)
        key = self.get_key(key_data)

        if key_action == "press":
            # Gestione del caps lock
            if key_data is Key.caps_lock:
                if self.caps_lock_state:
                    self.controller.release(key_data)
                else:
                    self.controller.press(key_data)
                self.caps_lock_state = not self.caps_lock_state
            else:
                # Gestione dei tasti speciali
                if self.is_special_key(key_data):  # Special key handler
                    if Key.alt_gr in self.pressed_keys:
                        self.controller.release(Key.alt_gr)
                        self.pressed_keys.discard(Key.alt_gr)
                    if Key.alt in self.pressed_keys:
                        self.controller.release(Key.alt)
                        self.pressed_keys.discard(Key.alt)

                self.controller.press(key)
                self.pressed_keys.add(key)
        elif key_action == "release":
            if key in self.pressed_keys:
                self.controller.release(key)
                self.pressed_keys.discard(key)

    def __str__(self):
        return "ClientKeyboardController"


class ClientMouseController(IMouseController):

    def __init__(self, context: IClientContext | IScreenContext):
        self.context = context
        self.mouse = MouseController()
        self.client_info = context.get_client_info_obj

        self.server_screen_width = 0
        self.server_screen_height = 0

        self.pressed = False
        self.last_press_time = -99
        self.doubleclick_counter = 0

        self.scroll_thread = ThreadPoolExecutor(max_workers=5)

        self.target_x = None
        self.target_y = None
        self.stop_event = threading.Event()

        # Smoothing thread
        self.smoothing_thread: Optional[threading.Thread] = None

        self.log = Logger.get_instance().log

    def stop(self):
        self.stop_event.set()
        self.smoothing_thread.join()
        self.stop_event.clear()

    def start(self):
        self.smoothing_thread = threading.Thread(target=self._smoothing_loop, daemon=True)
        self.smoothing_thread.start()

    def _get_server_screen_size(self):
        """
        Recupera la dimensione dello screen server dal contesto client.
        """
        if self.client_info() and self.client_info().screen_size:
            if not self.server_screen_width or not self.server_screen_height:
                self.server_screen_width, self.server_screen_height = self.client_info().server_screen_size

    def _set_target_position(self, x, y):
        self.target_x = x
        self.target_y = y
        if not self.context.get_state():
            self._smooth_logic(0, 0, 0.5)

    def _smooth_logic(self, last_x, last_y, speed_factor=0.1):
        current_x, current_y = self.mouse.position
        dx = self.target_x - current_x
        dy = self.target_y - current_y

        move_x = current_x + dx * speed_factor
        move_y = current_y + dy * speed_factor

        # Se la distanza è molto piccola, vai direttamente sul target
        dist = math.sqrt(dx * dx + dy * dy)
        if dist < 0.1 or ((last_x == move_x or last_y == move_y) and dist < 10):
            self.mouse.position = (self.target_x, self.target_y)
            return None, None
        else:
            # Muovi il cursore di una frazione della distanza rimanente
            self.mouse.position = (move_x, move_y)
            return move_x, move_y

    def _smoothing_loop(self, refresh_rate=0.001, speed_factor=0.1):
        """
        Loop eseguito in un thread separato.
        Muove gradualmente il cursore verso la posizione target.
        refresh_rate: tempo di sleep tra un frame e l'altro (in secondi).
        speed_factor: quanto velocemente il cursore si muove verso il target.
        """
        last_move_x = 0
        last_move_y = 0
        while not self.stop_event.is_set():
            if self.context.get_state() and self.target_x is not None and self.target_y is not None:
                x, y = self._smooth_logic(last_move_x, last_move_y, speed_factor)
                if x is not None and y is not None:
                    last_move_x = x
                    last_move_y = y

            time.sleep(refresh_rate)

    def process_mouse_command(self, x, y, mouse_action, is_pressed):
        self._get_server_screen_size()
        # Converte x,y in float se necessario
        if isinstance(x, str) or isinstance(y, str):
            try:
                x = float(x)
                y = float(y)
            except ValueError:
                self.log(f"Invalid mouse position: {x}, {y}", Logger.ERROR)
                return

        if mouse_action == "position":
            target_x = max(0, min(x * self.client_info().screen_size[0], self.client_info().screen_size[0]))
            target_y = max(0, min(y * self.client_info().screen_size[1], self.client_info().screen_size[1]))
            self._set_target_position(target_x, target_y)

        elif mouse_action == "move":
            # Denormalize the x and y values
            scale_x = self.server_screen_width / self.client_info().screen_size[0]
            scale_y = self.server_screen_height / self.client_info().screen_size[1]
            dx = x * scale_x
            dy = y * scale_y
            current_x, current_y = self.mouse.position
            # Imposta il target come posizione attuale + dx, dy
            self._set_target_position(current_x + dx, current_y + dy)

        elif mouse_action == "click":
            self.handle_click(Button.left, is_pressed)

        elif mouse_action == "right_click":
            self.mouse.click(Button.right)

        elif mouse_action == "scroll":
            self.scroll_thread.submit(self._smooth_scroll, x, y, 0.01, 5)

    def handle_click(self, button, is_pressed):
        current_time = time.time()
        if self.pressed and not is_pressed:
            self.mouse.release(button)
            self.pressed = False
        elif not self.pressed and is_pressed:
            # Se c'è un doppio click in <0.2s, inviamo un double-click effettivo
            if current_time - self.last_press_time < 0.2:
                self.mouse.click(button, 2 + self.doubleclick_counter)
                self.doubleclick_counter = 0 if self.doubleclick_counter == 2 else 2
                self.pressed = False
            else:
                self.mouse.press(button)
                self.doubleclick_counter = 0
                self.pressed = True
            self.last_press_time = current_time

    def _smooth_scroll(self, x, y, delay=0.01, steps=5):
        for _ in range(steps):
            self.mouse.scroll(x, y)
            time.sleep(delay)

    def get_current_position(self) -> tuple:
        return self.mouse.position

    def set_position(self, x: int | float, y: int | float) -> None:
        self.mouse.position = (x, y)

    def move(self, dx: int | float, dy: int | float) -> None:
        self.mouse.move(dx, dy)

    def __str__(self):
        return "ClientMouseController"


class ClientClipboardController(IClipboardController):

    def __init__(self, context: IClientContext):
        self.context = context
        self.logger = Logger.log

    def get_clipboard_data(self) -> str:
        return pyperclip.paste()

    def set_clipboard_data(self, data: str) -> None:
        pyperclip.copy(data)