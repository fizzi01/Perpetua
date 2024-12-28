import signal
import subprocess
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import pyperclip
from pynput import mouse
import Quartz
from AppKit import (NSPasteboard,
                    NSFilenamesPboardType,
                    NSEventTypeGesture,
                    NSEventTypeBeginGesture,
                    NSEventTypeSwipe, NSEventTypeRotate,
                    NSEventTypeMagnify)
import os

import threading
import time

import keyboard as hotkey_controller
from pynput.mouse import Button, Controller as MouseController
from pynput.mouse import Listener as MouseListener
from pynput.keyboard import Listener as KeyboardListener, Key, KeyCode, Controller as KeyboardController

from client.state.ClientState import HiddleState
from utils.Interfaces import IServerContext, IMessageService, IHandler, IEventBus, IMouseController, \
    IClipboardController, IFileTransferContext, IFileTransferService, IClientContext, IScreenContext, \
    IKeyboardController, IControllerContext, IMouseListener

from utils.Logging import Logger
from utils.net.netData import *


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
        self.logger = Logger.log

    def get_clipboard_data(self) -> str:
        return pyperclip.paste()

    def set_clipboard_data(self, data: str) -> None:
        pyperclip.copy(data)


class ServerMouseListener(IMouseListener):
    IGNORE_NEXT_MOVE_EVENT = 0.001
    MAX_DXDY_THRESHOLD = 200
    SCREEN_CHANGE_DELAY = 0.001
    EMULATION_STOP_DELAY = 0.7
    WARP_DELAY = 0.003

    def __init__(self,
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
        self.screen_treshold = screen_threshold

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

        self._listener = MouseListener(on_move=self.on_move, on_scroll=self.on_scroll, on_click=self.on_click,
                                       darwin_intercept=self.mouse_suppress_filter)

    def get_position(self):
        return self.x_print, self.y_print

    def get_listener(self):
        return self._listener

    def start(self):
        # Get the mouse controller from the context
        if isinstance(self.context, IControllerContext):
            self.mouse_controller = self.context.mouse_controller
        else:   # In case of wrong context, cancel the start
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

    """
    Filter for mouse events and blocks them system wide if not in screen
    Return event to not block it
    """

    def mouse_suppress_filter(self, event_type, event):

        screen = self.context.get_active_screen()

        if screen:
            gesture_events = []
            # Tenta di aggiungere le costanti degli eventi gestuali
            try:
                gesture_events.extend([
                    NSEventTypeGesture,
                    NSEventTypeMagnify,
                    NSEventTypeSwipe,
                    NSEventTypeRotate,
                    NSEventTypeBeginGesture,
                ])
            except AttributeError:
                pass

            # Aggiungi i valori numerici per le costanti mancanti
            gesture_events.extend([
                29,  # kCGEventGesture
            ])

            if event_type in [
                Quartz.kCGEventLeftMouseDown,
                Quartz.kCGEventRightMouseDown,
                Quartz.kCGEventOtherMouseDown,
                Quartz.kCGEventLeftMouseDragged,
                Quartz.kCGEventRightMouseDragged,
                Quartz.kCGEventOtherMouseDragged,
                Quartz.kCGEventScrollWheel,

            ] + gesture_events:
                pass
            else:
                return event
        else:
            return event

    def warp_cursor_to_center(self):
        while not self.stop_warp.is_set():
            try:
                if self.to_warp.is_set() and self.context.is_transition_in_progress():
                    current_time = time.time()

                    if self.stop_emulation and current_time <= self.stop_emulation_timeout:
                        return

                    # Check if the screen change is in progress, if yes don't warp the cursor
                    if self.screen_change_in_progress and current_time <= self.screen_change_timeout:
                        return

                    self.ignore_move_events_until = time.time() + self.IGNORE_NEXT_MOVE_EVENT
                    center_x = self.screen_width // 2
                    center_y = self.screen_height // 2

                    # Last check before moving the cursor
                    if not self.stop_emulation and self.to_warp.is_set():
                        self.mouse_controller.set_position(center_x, center_y)
                        self.last_x, self.last_y = self.mouse_controller.get_current_position()
                        self.to_warp.clear()
            except TypeError:
                pass
            except AttributeError:
                pass

            time.sleep(self.WARP_DELAY)

    def on_move(self, x, y):
        # Check if cursor is in warped position
        if self.to_warp.is_set() and x == self.screen_width // 2 and y == self.screen_height // 2:
            self.to_warp.clear()
            return True # Skip the event

        current_time = time.time()

        if self.stop_emulation and current_time > self.stop_emulation_timeout and len(self.buttons_pressed) == 0:
            self.stop_emulation = False

        if not self.stop_emulation and self.ignore_move_events_until > current_time:
            self.last_x = x
            self.last_y = y
            return True

        # Check if the screen change is in progress and if the timeout has expired
        if self.screen_change_in_progress and current_time > self.screen_change_timeout:
            self.screen_change_in_progress = False

        # Calcola il movimento relativo
        dx = 0
        dy = 0

        if self.last_x is not None and self.last_y is not None:
            dx = x - self.last_x
            dy = y - self.last_y

        # Ignora movimenti anomali e troppo grandi (quando cursore bloccato possono esserci movimenti anomali)
        if abs(dx) > self.MAX_DXDY_THRESHOLD or abs(dy) > self.MAX_DXDY_THRESHOLD:
            self.last_x = x
            self.last_y = y
            return True

        # Aggiorna l'ultima posizione
        self.last_x = x
        self.last_y = y

        # Controlla se il cursore è al bordo
        at_right_edge = x >= self.screen_width - 1
        at_left_edge = x <= 0
        at_bottom_edge = y >= self.screen_height - 1
        at_top_edge = y <= 0

        screen = self.context.get_active_screen()
        client = self.context.get_client(screen)
        # client_screen = self.context.get_clients.get_screen_size(screen)
        is_transmitting = self.context.is_transition_in_progress()

        if screen and client and is_transmitting:

            if not self.buttons_pressed and not self.stop_emulation:

                self.x_print += dx
                self.y_print += dy

                # Clip the cursor position to the screen bounds
                self.x_print = max(0, min(self.x_print, self.screen_width))
                self.y_print = max(0, min(self.y_print, self.screen_height))

                self.send(screen, format_command(
                    f"mouse position {self.x_print / self.screen_width} {self.y_print / self.screen_height}"))
                self.to_warp.set()
            elif self.stop_emulation or self.buttons_pressed:
                normalized_x = x / self.screen_width
                normalized_y = y / self.screen_height
                self.send(screen, format_command(f"mouse position {normalized_x} {normalized_y}"))

        elif not self.buttons_pressed and not self.screen_change_in_progress:
            # Quando si attraversa un bordo, invia una posizione assoluta normalizzata
            if at_right_edge:
                self.stop_emulation = False
                self.screen_change_in_progress = True
                self.screen_change_timeout = time.time() + self.SCREEN_CHANGE_DELAY
                self.event_bus.publish(IEventBus.SCREEN_CHANGE_EVENT, "right")
                normalized_x = 0  # Entra dal bordo sinistro del client
                normalized_y = y / self.screen_height
                self.send("right", format_command(f"mouse position {normalized_x} {normalized_y}"))
                self.x_print = normalized_x * self.screen_width
                self.y_print = normalized_y * self.screen_height
            elif at_left_edge:
                self.stop_emulation = False
                self.screen_change_in_progress = True
                self.screen_change_timeout = time.time() + self.SCREEN_CHANGE_DELAY
                self.event_bus.publish(IEventBus.SCREEN_CHANGE_EVENT, "left")
                normalized_x = 1  # Entra dal bordo destro del client
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
                normalized_y = 0  # Entra dal bordo superiore del client
                self.send("down", format_command(f"mouse position {normalized_x} {normalized_y}"))
                self.x_print = normalized_x * self.screen_width
                self.y_print = normalized_y * self.screen_height
            elif at_top_edge:
                self.stop_emulation = False
                self.screen_change_in_progress = True
                self.screen_change_timeout = time.time() + self.SCREEN_CHANGE_DELAY
                self.event_bus.publish(IEventBus.SCREEN_CHANGE_EVENT, "up")
                normalized_x = x / self.screen_width
                normalized_y = 1  # Entra dal bordo inferiore del client
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

        # Gestisce il passaggio da stima della posizione con cursore bloccato,
        # a posizione assoluta con cursore libero. La stima della posizione è
        # necessaria per evitare che il cursore vada sui bordi ad inizio transizione
        # Stima e posizione reale sono equivalenti allo stato attuale. Solo che la stima
        # blocca il cursore al centro, e siccome un click evita il blocco del cursore si
        # fa fallback alla posizione assoluta reale (Sono intercambiabili, ma la stima è necessaria per la transizione)
        if button == mouse.Button.left and pressed and screen and client and is_transmitting:
            self.buttons_pressed.add(button)

            # Move cursor to the x_y saved in the last move event
            if not self.stop_emulation:
                self.to_warp.clear()
                self.mouse_controller.set_position(self.x_print, self.y_print)

            self.stop_emulation = True
            self.stop_emulation_timeout = time.time() + self.EMULATION_STOP_DELAY
        else:
            self.buttons_pressed.discard(button)
            current_time = time.time()
            if self.stop_emulation and current_time > self.stop_emulation_timeout:
                self.stop_emulation = False
                self.to_warp.clear()

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
    :param get_clients: Function to get the clients of the current screen
    :param get_active_screen: Function to get the active screen
    """

    def __init__(self, context: IServerContext | IFileTransferContext, message_service: IMessageService,
                 event_bus: IEventBus):

        self.context = context
        self.event_bus = event_bus

        self.send = message_service.send_keyboard
        self.logger = Logger.log

        self.file_transfer_handler: IFileTransferService | None = None

        self.command_pressed = False

        self._listener = KeyboardListener(on_press=self.on_press, on_release=self.on_release,
                                          darwin_intercept=self.keyboard_suppress_filter)

        self._caps_lock = False

    def get_listener(self):
        return self._listener

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
        try:
            # Execute AppleScript to get the active window directory
            script = """
            tell application "System Events"
                set frontApp to name of first application process whose frontmost is true
            end tell
            if frontApp is "Finder" then
                tell application "Finder"
                    try
                        -- Se c'è una cartella selezionata, usa quella
                        set selectedItems to selection
                        if (count of selectedItems) > 0 then
                            set selectedItem to item 1 of selectedItems
                            if (class of selectedItem) is folder then
                                return POSIX path of (selectedItem as alias)
                            end if
                        end if

                        -- Altrimenti, usa la finestra attiva
                        if (count of Finder windows) > 0 then
                            set currentFolder to (target of Finder window 1 as alias)
                            return POSIX path of currentFolder
                        else
                            -- Se nessuna finestra è attiva, ritorna la Scrivania
                            return POSIX path of (path to desktop folder)
                        end if
                    on error
                        -- Fallback al Desktop in caso di errore
                        return POSIX path of (path to desktop folder)
                    end try
                end tell
            else
                return ""
            end if
            """

            result = subprocess.check_output(["osascript", "-e", script]).decode().strip()
            return result if result else None
        except Exception as e:
            print(f"Errore: {e}")
            return None

    def keyboard_suppress_filter(self, event_type, event):
        screen = self.context.get_active_screen()

        flags = Quartz.CGEventGetFlags(event)
        caps_lock = flags & Quartz.kCGEventFlagMaskAlphaShift

        # Ottieni il codice del tasto dall'evento
        media_volume_event = 14

        if screen:
            if caps_lock != 0:
                self.logger("Caps Lock is pressed")
                return event
            elif event_type == Quartz.kCGEventKeyDown:  # Key press event 
                pass
            elif event_type == media_volume_event:
                pass
            else:
                return event
        else:
            return event

    def on_press(self, key: Key | KeyCode | None):
        screen = self.context.get_active_screen()
        client = self.context.get_client(screen)

        if isinstance(key, Key):
            data = key.name
        else:
            data = key.char

        # Check if command + v is pressed
        if key == Key.cmd:
            self.command_pressed = True
        elif key == Key.esc and self.command_pressed:  # KILL SWITCH _DEBUG_ONLY_
            os.kill(os.getpid(), signal.SIGTERM)
        elif data == "v" and self.command_pressed:
            current_dir = self.get_current_clicked_directory()
            if current_dir and self.file_transfer_handler:
                self.file_transfer_handler.handle_file_paste(current_dir)

        if screen and client:
            self.send(screen, format_command(f"keyboard press {data}"))

    def on_release(self, key: Key | KeyCode | None):
        screen = self.context.get_active_screen()
        client = self.context.get_client(screen)

        if isinstance(key, Key):
            data = key.name
        else:
            data = key.char

        # Check if command is released
        if key == Key.cmd:
            self.command_pressed = False

        if screen and client:
            self.send(screen, format_command(f"keyboard release {data}"))

    def __str__(self):
        return "ServerKeyboardListener"


class ServerClipboardListener(IHandler):
    def __init__(self,
                 context: IServerContext | IControllerContext | IFileTransferContext,
                 message_service: IMessageService,
                 event_bus: IEventBus
                 ):

        self.send = message_service.send_clipboard

        self.context = context
        self.event_bus = event_bus

        self._thread = None

        self.clipboard_controller: Optional[IClipboardController] = None
        self.last_clipboard_content: Optional[str] = None
        self.last_clipboard_files = None

        self._stop_event = threading.Event()
        self.logger = Logger.log

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
        return self._thread.is_alive()

    @staticmethod
    def get_clipboard_files():
        try:
            pb = NSPasteboard.generalPasteboard()
            types = pb.types()

            # Controlla se ci sono file o directory nella clipboard
            if NSFilenamesPboardType in types:
                file_paths = pb.propertyListForType_(NSFilenamesPboardType)
                results = []
                for path in file_paths:
                    if os.path.isdir(path):
                        results.append((path, "directory"))
                    elif os.path.isfile(path):
                        results.append((path, "file"))
                    else:
                        results.append((path, "unknown"))
                return results
            return None
        except AttributeError:
            return None
        except TypeError:
            return None
        except OSError:
            return None

    @staticmethod
    def get_file_info(file_path):
        try:
            if os.path.isfile(file_path):
                return {
                    'file_name': os.path.basename(file_path),
                    'file_size': os.path.getsize(file_path),
                    'file_path': file_path
                }
            return None
        except OSError:
            return None
        except TypeError:
            return None
        except AttributeError:
            return None

    def _run(self):
        while not self._stop_event.is_set():
            try:
                current_clipboard_content = self.clipboard_controller.get_clipboard_data()
                current_clipboard_files = self.get_clipboard_files()

                if current_clipboard_files and current_clipboard_files != self.last_clipboard_files:
                    # Takes the last file in the list only if it's a file
                    if current_clipboard_files[-1][1] == "file":
                        file_info = self.get_file_info(current_clipboard_files[-1][0])
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

                elif current_clipboard_content != self.last_clipboard_content:
                    # Invia il contenuto della clipboard a tutti i client
                    self.send("all", format_command("clipboard ") + current_clipboard_content)
                    self.last_clipboard_content = current_clipboard_content

                time.sleep(0.5)
            except Exception as e:
                # Reset the clipboard content if an error occurs
                self.last_clipboard_content = self.clipboard_controller.get_clipboard_data()
                self.logger(f"[CLIPBOARD] {e}", Logger.ERROR)

    def __str__(self):
        return "ServerClipboardListener"


class ClientKeyboardController(IKeyboardController):

    def __init__(self, context = IClientContext):
        self.context = context  # Reserved for future use
        self.pressed_keys = set()

        # self.key_filter = {  # Darwin specific key codes
        #     "alt": 0x3a,  # Option key
        #     "option": 0x3a,  # Option key
        #     "caps_lock": 0x39,  # Caps lock key
        #     "media_volume_mute": 0x4a,  # Mute key
        #     "media_volume_down": "volume down",  # Volume down key
        #     "media_volume_up": "volume up",  # Volume up key
        #     ",": "comma",
        #     "+": "plus"
        # }
        self.key_filter = {
        }

        self.not_shift = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0", ",", "+", "-"]

        # Needed on macos silicon
        self.hotkey_filter = {"1": 0x12,
                              "2": 0x13,
                              "3": 0x14,
                              "4": 0x15,
                              "5": 0x17,
                              "6": 0x16,
                              "7": 0x1a,
                              "8": 0x1c,
                              "9": 0x19,
                              "0": 0x1d,
                              "+": 0x1e,
                              ",": 0x2b,
                              "-": 0x2C,
                              "alt_l": 0x3a}

        self.keyboard = KeyboardController()
        self.hotkey = hotkey_controller
        self.logger = Logger.log

    def start(self):
        pass

    def stop(self):
        pass

    def data_filter(self, key_data):
        if key_data in self.key_filter:
            return self.key_filter[key_data]
        return key_data

    def get_hotkey_filter(self, key_data):
        if key_data in self.hotkey_filter:
            return self.hotkey_filter[key_data]
        return key_data

    @staticmethod
    def get_key(key_data: str):
        try:
            key = Key[key_data]
            return key
        except KeyError:
            return key_data

    @staticmethod
    def is_alt_gr(key_data):
        return key_data == "alt_gr" or key_data == "alt_r"

    @staticmethod
    def is_special_key(key_data):
        return key_data in ["shift", "alt_l", "cmd"]

    def process_key_command(self, key_data, key_action):
        key_data = self.data_filter(key_data)

        if key_action == "press":
            if self.is_alt_gr(key_data):
                self.keyboard.release(Key.ctrl_r)
            if self.is_special_key(key_data):
                self.pressed_keys.add(key_data)
                self.keyboard.press(self.get_key(key_data))
            else:
                if len(self.pressed_keys) != 0 and self.get_key(key_data) in self.not_shift:
                    key_data = self.get_hotkey_filter(key_data)
                    hotkey = []
                    for key in self.pressed_keys:
                        hotkey.append(self.get_hotkey_filter(key))
                    hotkey.append(key_data)
                    self.hotkey.press_and_release(hotkey)
                else:
                    self.keyboard.press(self.get_key(key_data))
        elif key_action == "release":
            if self.is_special_key(key_data) and len(self.pressed_keys) > 0:
                self.pressed_keys.remove(key_data)
            self.keyboard.release(self.get_key(key_data))

    def __str__(self):
        return "ClientKeyboardController"


class ClientMouseController(IMouseController):
    def __init__(self, context: IClientContext | IScreenContext):

        self.context = context

        self.mouse = MouseController()
        self.screen = context.get_client_screen_size

        self.client_info = context.get_client_info

        self.server_screen_width = 0
        self.server_screen_height = 0

        self.pressed = False
        self.last_press_time = -99
        self.doubleclick_counter = 0

        # Mouse scrool thread pool
        self.scroll_thread = ThreadPoolExecutor(max_workers=5)

    def start(self):
        pass

    def stop(self):
        self.scroll_thread.shutdown()

    def get_server_screen_size(self):
        if "screen_size" in self.client_info():
            if not self.server_screen_width or not self.server_screen_height:
                self.server_screen_width, self.server_screen_height = self.client_info().get("screen_size", (0, 0))

    def process_mouse_command(self, x, y, mouse_action, is_pressed):
        self.get_server_screen_size()

        # Check if x and y are str
        if isinstance(x, str) or isinstance(y, str):
            try:
                x, y = float(x), float(y)
            except ValueError:
                self.context.log(f"Invalid mouse position: {x}, {y}", Logger.ERROR)
                return

        if mouse_action == "position":
            target_x = max(0, min(x * self.screen()[0], self.screen()[0]))  # Ensure target_x is within screen bounds
            target_y = max(0,
                           min(y * self.screen()[1], self.screen()[1]))  # Ensure target_y is within screen bounds

            self.mouse.position = (target_x, target_y)
        elif mouse_action == "move":
            scale_x = self.server_screen_width / self.screen()[0]
            scale_y = self.server_screen_height / self.screen()[1]
            dx = int(x * scale_x)
            dy = int(y * scale_y)
            self.mouse.position = (self.mouse.position[0] + dx, self.mouse.position[1] + dy)
        elif mouse_action == "click":
            self.handle_click(Button.left, is_pressed)
        elif mouse_action == "right_click":
            self.mouse.click(Button.right)
        elif mouse_action == "scroll":
            self.scroll_thread.submit(self.smooth_scroll, x, y, 0.01, 5)

    def handle_click(self, button, is_pressed):
        current_time = time.time()
        if self.pressed and not is_pressed:

            self.mouse.release(button)
            self.pressed = False
        elif not self.pressed and is_pressed:
            if current_time - self.last_press_time < 0.2:
                self.mouse.click(button, 2 + self.doubleclick_counter)  # Perform a double click
                self.doubleclick_counter = 0 if self.doubleclick_counter == 2 else 2
                self.pressed = False
            else:
                self.mouse.press(button)
                self.doubleclick_counter = 0
                self.pressed = True
            self.last_press_time = current_time

    def smooth_scroll(self, x, y, delay=0.01, steps=5):
        """Smoothly scroll the mouse."""
        dx, dy = x, y
        for _ in range(steps):
            self.mouse.scroll(dx, dy)
            time.sleep(delay)

    def get_current_position(self) -> tuple:
        return self.mouse.position

    def set_position(self, x: int | float, y: int | float) -> None:
        self.mouse.position = (x, y)

    def move(self, x: int | float, y: int | float) -> None:
        self.mouse.move(x, y)

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


class ClientMouseListener(IHandler):
    def __init__(self, context: IClientContext, message_service: IMessageService, event_bus: IEventBus,
                 screen_width: int, screen_height: int, screen_threshold: int = 5):

        self.context = context
        self.event_bus = event_bus

        self.screen_width = screen_width
        self.screen_height = screen_height
        self.threshold = screen_threshold

        self.send = message_service.send_mouse
        self._listener = MouseListener(on_move=self.handle_mouse)

    def get_listener(self):
        return self._listener

    def start(self):
        self._listener.start()

    def stop(self):
        self._listener.stop()

    def is_alive(self) -> bool:
        return self._listener.is_alive()

    def handle_mouse(self, x, y):

        if self.context.get_state():  # Return True if the client is in Controlled state
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
    def __init__(self, context: IClientContext | IFileTransferContext, message_service: IMessageService,
                 event_bus: IEventBus):
        self.context = context
        self.send = message_service.send_keyboard
        self.event_bus = event_bus

        self.file_transfer_handler: IFileTransferService | None = None
        self.command_pressed = False

        self._listener = KeyboardListener(on_press=self.on_press, on_release=self.on_release)

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
        try:
            # Execute AppleScript to get the active window directory
            script = """
                    tell application "System Events"
                        set frontApp to name of first application process whose frontmost is true
                    end tell
                    if frontApp is "Finder" then
                        tell application "Finder"
                            try
                                -- Se c'è una cartella selezionata, usa quella
                                set selectedItems to selection
                                if (count of selectedItems) > 0 then
                                    set selectedItem to item 1 of selectedItems
                                    if (class of selectedItem) is folder then
                                        return POSIX path of (selectedItem as alias)
                                    end if
                                end if
    
                                -- Altrimenti, usa la finestra attiva
                                if (count of Finder windows) > 0 then
                                    set currentFolder to (target of Finder window 1 as alias)
                                    return POSIX path of currentFolder
                                else
                                    -- Se nessuna finestra è attiva, ritorna la Scrivania
                                    return POSIX path of (path to desktop folder)
                                end if
                            on error
                                -- Fallback al Desktop in caso di errore
                                return POSIX path of (path to desktop folder)
                            end try
                        end tell
                    else
                        return ""
                    end if
                    """

            result = subprocess.check_output(["osascript", "-e", script]).decode().strip()
            return result if result else None
        except Exception as e:
            print(f"Errore: {e}")
            return None

    def on_press(self, key: Key | KeyCode | None):

        if isinstance(key, Key):
            data = key.name
        else:
            data = key.char

        # Check if command + v is pressed
        if key == Key.cmd:
            self.command_pressed = True
        elif data == "v" and self.command_pressed:
            current_dir = self.get_current_clicked_directory()
            if current_dir:
                self.file_transfer_handler.handle_file_paste(current_dir)

    def on_release(self, key: Key | KeyCode | None):

        # Check if command is released
        if key == Key.cmd:
            self.command_pressed = False


class ClientClipboardListener(IHandler):
    def __init__(self, context: IClientContext | IFileTransferContext, message_service: IMessageService,
                 event_bus: IEventBus):

        self.context = context
        self.event_bus = event_bus
        self.send = message_service.send_clipboard

        self._thread = None

        self.clipboard_controller: Optional[IClipboardController] = None
        self.last_clipboard_content: Optional[str] = None
        self.last_clipboard_files = None
        self._stop_event = threading.Event()
        self.logger = Logger.log

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
        return self._thread.is_alive()

    @staticmethod
    def get_clipboard_files():
        pb = NSPasteboard.generalPasteboard()
        types = pb.types()

        # Controlla se ci sono file o directory nella clipboard
        if NSFilenamesPboardType in types:
            file_paths = pb.propertyListForType_(NSFilenamesPboardType)
            results = []
            for path in file_paths:
                if os.path.isdir(path):
                    results.append((path, "directory"))
                elif os.path.isfile(path):
                    results.append((path, "file"))
                else:
                    results.append((path, "unknown"))
            return results
        return None

    @staticmethod
    def get_file_info(file_path):
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
                    # Takes the last file in the list only if it's a file
                    if current_clipboard_files[-1][1] == "file":
                        file_info = self.get_file_info(current_clipboard_files[-1][0])
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
