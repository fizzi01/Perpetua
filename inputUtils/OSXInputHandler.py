import math
import subprocess
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

import pyperclip
from pynput import mouse
import Quartz
from AppKit import (NSPasteboard,
                    NSFilenamesPboardType,
                    NSEventTypeGesture,
                    NSEventTypeBeginGesture,
                    NSEventTypeEndGesture,
                    NSEventTypeSwipe, NSEventTypeRotate,
                    NSEventTypeMagnify)
import os

import threading
import time

import keyboard as hotkey_controller
from pynput.mouse import Button, Controller as MouseController
from pynput.mouse import Listener as MouseListener
from pynput.keyboard import Listener as KeyboardListener, Key, KeyCode, Controller as KeyboardController

from client import ClientState
from client.ClientState import HiddleState
from config.ServerConfig import Clients
from inputUtils import HandlerInterface
from inputUtils.FileTransferEventHandler import FileTransferEventHandler

from utils.Logging import Logger
from utils.netData import *

from network.IOManager import QueueManager


class ServerMouseController(HandlerInterface):
    def start(self):
        pass

    def stop(self):
        pass


class ServerMouseListener(HandlerInterface):
    IGNORE_NEXT_MOVE_EVENT = 0.01
    MAX_DXDY_THRESHOLD = 150
    SCREEN_CHANGE_DELAY = 0.001
    EMULATION_STOP_DELAY = 0.5

    def __init__(self, change_screen_function: Callable, get_active_screen: Callable,
                 get_status: Callable,
                 screen_width: int, screen_height: int, screen_threshold: int = 5,
                 clients: Clients = None):

        self.active_screen = get_active_screen
        self.change_screen = change_screen_function
        self.get_trasmission_status = get_status
        self.clients = clients
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.screen_treshold = screen_threshold

        self.logger = Logger.get_instance().log
        self.send = QueueManager(None).send_mouse

        self.last_x = None
        self.last_y = None
        self.x_print = 0
        self.y_print = 0
        self.mouse_controller = MouseController()
        self.buttons_pressed = set()
        self.stop_emulation = False
        self.stop_emulation_timeout = 0
        self.screen_change_in_progress = False
        self.screen_change_timeout = 0
        self.ignore_move_events_until = 0

        self._listener = MouseListener(on_move=self.on_move, on_scroll=self.on_scroll, on_click=self.on_click,
                                       darwin_intercept=self.mouse_suppress_filter)

        self.move_threshold = 2  # Minimum movement required to trigger on_move

    def get_position(self):
        return self.x_print, self.y_print

    def get_listener(self):
        return self._listener

    def start(self):
        self._listener.start()

    def stop(self):
        if self.is_alive():
            self._listener.stop()

    def is_alive(self):
        return self._listener.is_alive()

    """
    Filter for mouse events and blocks them system wide if not in screen
    Return event to not block it
    """

    def mouse_suppress_filter(self, event_type, event):

        screen = self.active_screen()

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
        current_time = time.time()

        if self.stop_emulation and current_time <= self.stop_emulation_timeout:
            return

        # Check if the screen change is in progress, if yes don't warp the cursor
        if self.screen_change_in_progress and current_time <= self.screen_change_timeout:
            return

        self.ignore_move_events_until = time.time() + self.IGNORE_NEXT_MOVE_EVENT
        center_x = self.screen_width // 2
        center_y = self.screen_height // 2
        self.mouse_controller.position = (center_x, center_y)
        self.last_x, self.last_y = self.mouse_controller.position

    def on_move(self, x, y):

        current_time = time.time()
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

        screen = self.active_screen()
        client = self.clients.get_connection(screen)
        client_screen = self.clients.get_screen_size(screen)
        is_transmitting = self.get_trasmission_status()

        if screen and client and is_transmitting:

            if not self.buttons_pressed and not self.stop_emulation:
                scale_x = client_screen[0] / self.screen_width
                scale_y = client_screen[1] / self.screen_height
                dx *= scale_x * 1.5
                dy *= scale_y * 1.5

                # Arrotondo a un intero dx e dy che deve essere minimo 1 in valore assoluto (ma preserva segno)
                # Se sotto 0.01 arrotonda a 0
                if abs(dx) < 0.5:
                    dx = 0
                else:
                    dx = math.copysign(max(1, abs(round(dx))), dx)

                if abs(dy) < 0.5:
                    dy = 0
                else:
                    dy = math.copysign(max(1, abs(round(dy))), dy)

                self.x_print += dx
                self.y_print += dy

                # Clip the cursor position to the screen bounds
                self.x_print = max(0, min(self.x_print, self.screen_width))
                self.y_print = max(0, min(self.y_print, self.screen_height))

                self.send(screen, format_command(
                    f"mouse position {self.x_print / self.screen_width} {self.y_print / self.screen_height}"))

                self.warp_cursor_to_center()
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
                self.change_screen("right")
                normalized_x = 0  # Entra dal bordo sinistro del client
                normalized_y = y / self.screen_height
                self.send("right", format_command(f"mouse position {normalized_x} {normalized_y}"))
                self.x_print = normalized_x * self.screen_width
                self.y_print = normalized_y * self.screen_height
            elif at_left_edge:
                self.stop_emulation = False
                self.screen_change_in_progress = True
                self.screen_change_timeout = time.time() + self.SCREEN_CHANGE_DELAY
                self.change_screen("left")
                normalized_x = 1  # Entra dal bordo destro del client
                normalized_y = y / self.screen_height
                self.send("left", format_command(f"mouse position {normalized_x} {normalized_y}"))
                self.x_print = normalized_x * self.screen_width
                self.y_print = normalized_y * self.screen_height
            elif at_bottom_edge:
                self.stop_emulation = False
                self.screen_change_in_progress = True
                self.screen_change_timeout = time.time() + self.SCREEN_CHANGE_DELAY
                self.change_screen("down")
                normalized_x = x / self.screen_width
                normalized_y = 0  # Entra dal bordo superiore del client
                self.send("down", format_command(f"mouse position {normalized_x} {normalized_y}"))
                self.x_print = normalized_x * self.screen_width
                self.y_print = normalized_y * self.screen_height
            elif at_top_edge:
                self.stop_emulation = False
                self.screen_change_in_progress = True
                self.screen_change_timeout = time.time() + self.SCREEN_CHANGE_DELAY
                self.change_screen("up")
                normalized_x = x / self.screen_width
                normalized_y = 1  # Entra dal bordo inferiore del client
                self.send("up", format_command(f"mouse position {normalized_x} {normalized_y}"))
                self.x_print = normalized_x * self.screen_width
                self.y_print = normalized_y * self.screen_height

        if self.stop_emulation:
            self.x_print, self.y_print = x, y

        return True

    def on_click(self, x, y, button, pressed):

        screen = self.active_screen()
        client = self.clients.get_connection(screen)

        # Gestisce il passaggio da stima della posizione con cursore bloccato,
        # a posizione assoluta con cursore libero. La stima della posizione è
        # necessaria per evitare che il cursore vada sui bordi ad inizio transizione
        # Stima e posizione reale sono equivalenti allo stato attuale. Solo che la stima
        # blocca il cursore al centro, e siccome un click evita il blocco del cursore si
        # fa fallback alla posizione assoluta reale (Sono intercambiabili, ma la stima è necessaria per la transizione)
        if pressed and screen:
            self.buttons_pressed.add(button)

            # Move cursor to the x_y saved in the last move event
            if not self.stop_emulation:
                self.mouse_controller.position = (self.x_print, self.y_print)

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
        screen = self.active_screen()
        client = self.clients.get_connection(screen)
        if screen and client:
            self.send(screen, format_command(f"mouse scroll {dx} {dy}"))
        return True

    def __str__(self):
        return "ServerMouseListener"


class ServerKeyboardListener(HandlerInterface):
    """
    :param get_clients: Function to get the clients of the current screen
    :param get_active_screen: Function to get the active screen
    """

    def __init__(self, get_clients: Callable, get_active_screen: Callable):
        self.clients = get_clients
        self.active_screen = get_active_screen
        self.send = QueueManager(None).send_keyboard
        self.logger = Logger.get_instance().log

        self.file_transfer_handler = FileTransferEventHandler()
        self.command_pressed = False

        self._listener = KeyboardListener(on_press=self.on_press, on_release=self.on_release,
                                          darwin_intercept=self.keyboard_suppress_filter)

        self._caps_lock = False

    def get_listener(self):
        return self._listener

    def start(self):
        self.logger("Starting keyboard listener")
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
        screen = self.active_screen()

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
        screen = self.active_screen()
        clients = self.clients(screen)

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
                self.file_transfer_handler.handle_file_paste(current_dir, self.file_transfer_handler.SERVER_REQUEST)

        if screen and clients:
            self.send(screen, format_command(f"keyboard press {data}"))

    def on_release(self, key: Key | KeyCode | None):
        screen = self.active_screen()
        clients = self.clients(screen)

        if isinstance(key, Key):
            data = key.name
        else:
            data = key.char

        # Check if command is released
        if key == Key.cmd:
            self.command_pressed = False

        if screen and clients:
            self.send(screen, format_command(f"keyboard release {data}"))

    def __str__(self):
        return "ServerKeyboardListener"


class ServerClipboardListener(HandlerInterface):
    def __init__(self, get_clients: Callable, get_active_screen: Callable):

        self.send = QueueManager(None).send_clipboard

        self.clients = get_clients
        self.active_screen = get_active_screen
        self._thread = None

        self.last_clipboard_content = pyperclip.paste()  # Inizializza con il contenuto attuale della clipboard
        self.last_clipboard_files = None

        self._stop_event = threading.Event()
        self.logger = Logger.get_instance().log
        self.file_transfer_handler = FileTransferEventHandler()

    def start(self):
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run)
        self._thread.start()

    def stop(self):
        self._stop_event.set()

    def is_alive(self):
        return self._thread.is_alive()

    @staticmethod
    def set_clipboard(content):
        pyperclip.copy(content)

    def get_clipboard(self):
        return self.last_clipboard_content

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
        except Exception:
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
        except Exception:
            return None

    def _run(self):
        while not self._stop_event.is_set():
            try:
                current_clipboard_content = pyperclip.paste()
                current_clipboard_files = self.get_clipboard_files()

                if current_clipboard_files and current_clipboard_files != self.last_clipboard_files:
                    # Takes the last file in the list only if it's a file
                    if current_clipboard_files[-1][1] == "file":
                        file_info = self.get_file_info(current_clipboard_files[-1][0])
                        if file_info:
                            self.file_transfer_handler.handle_file_copy(file_info,
                                                                        self.file_transfer_handler.LOCAL_SERVER_OWNERSHIP)
                            self.last_clipboard_files = current_clipboard_files

                elif current_clipboard_content != self.last_clipboard_content:
                    # Invia il contenuto della clipboard a tutti i client
                    self.send("all", format_command("clipboard ") + current_clipboard_content)
                    self.last_clipboard_content = current_clipboard_content

                time.sleep(0.5)
            except Exception as e:
                # Reset the clipboard content if an error occurs
                self.last_clipboard_content = pyperclip.paste()
                self.logger(f"[CLIPBOARD] {e}", Logger.ERROR)

    def __str__(self):
        return "ServerClipboardListener"


class ClientClipboardListener(HandlerInterface):
    def __init__(self):

        self.send = QueueManager(None).send_clipboard
        self._thread = None
        self.last_clipboard_content = pyperclip.paste()  # Inizializza con il contenuto attuale della clipboard
        self.last_clipboard_files = None
        self._stop_event = threading.Event()
        self.logger = Logger.get_instance().log
        self.file_transfer_handler = FileTransferEventHandler()

    def start(self):
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run)
        self._thread.start()

    def stop(self):
        self._stop_event.set()

    def is_alive(self):
        return self._thread.is_alive()

    def set_clipboard(self, content):
        pyperclip.copy(content)
        self.last_clipboard_content = content

    def get_clipboard(self):
        return self.last_clipboard_content

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
                current_clipboard_content = pyperclip.paste()
                current_clipboard_files = self.get_clipboard_files()

                if current_clipboard_files and current_clipboard_files != self.last_clipboard_files:
                    # Takes the last file in the list only if it's a file
                    if current_clipboard_files[-1][1] == "file":
                        file_info = self.get_file_info(current_clipboard_files[-1][0])
                        if file_info:
                            self.file_transfer_handler.handle_file_copy(file_info,
                                                                        self.file_transfer_handler.LOCAL_OWNERSHIP)
                            self.last_clipboard_files = current_clipboard_files

                elif current_clipboard_content != self.last_clipboard_content:
                    self.send(None, format_command("clipboard ") + current_clipboard_content)
                    self.last_clipboard_content = current_clipboard_content
                time.sleep(0.5)
            except Exception as e:
                self.logger(f"[CLIPBOARD] {e}", Logger.ERROR)

    def __str__(self):
        return "ClientClipboardListener"


class ClientKeyboardController(HandlerInterface):

    def __init__(self):
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
        self.logger = Logger.get_instance().log

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
        except Exception:
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


class ClientMouseController(HandlerInterface):
    def __init__(self, screen_width, screen_height, client_info: dict):
        self.mouse = MouseController()
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.client_info = client_info
        self.server_screen_width = 0
        self.server_screen_height = 0
        self.pressed = False
        self.last_press_time = -99
        self.doubleclick_counter = 0

        # Mouse scrool thread pool
        self.scroll_thread = ThreadPoolExecutor(max_workers=5)

        self.log = Logger.get_instance().log

    def start(self):
        pass

    def stop(self):
        pass

    def get_server_screen_size(self):
        if "screen_size" in self.client_info:
            if not self.server_screen_width or not self.server_screen_height:
                self.server_screen_width, self.server_screen_height = self.client_info["screen_size"]

    def process_mouse_command(self, x, y, mouse_action, is_pressed):
        self.get_server_screen_size()
        if mouse_action == "position":
            target_x = max(0, min(x * self.screen_width, self.screen_width))  # Ensure target_x is within screen bounds
            target_y = max(0,
                           min(y * self.screen_height, self.screen_height))  # Ensure target_y is within screen bounds

            self.mouse.position = (target_x, target_y)
        elif mouse_action == "move":
            scale_x = self.server_screen_width / self.screen_width
            scale_y = self.server_screen_height / self.screen_height
            dx = int(x * scale_x)
            dy = int(y * scale_y)
            self.mouse.position = (self.mouse.position[0] + dx, self.mouse.position[1] + dy)
        elif mouse_action == "click":
            self.handle_click(Button.left, is_pressed)
        elif mouse_action == "right_click":
            self.mouse.click(Button.right)
        elif mouse_action == "scroll":
            self.scroll_thread.submit(self.smooth_scroll, x, y)

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

    def __str__(self):
        return "ClientMouseController"


class ClientMouseListener(HandlerInterface):
    def __init__(self, screen_width, screen_height, threshold):
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.threshold = threshold
        self.send = QueueManager(None).send_mouse
        self.client_status = ClientState()
        self._listener = MouseListener(on_move=self.handle_mouse)
        self.logger = Logger.get_instance().log

    def get_listener(self):
        return self._listener

    def start(self):
        self._listener.start()

    def stop(self):
        self._listener.stop()

    def handle_mouse(self, x, y):

        if self.client_status.get_state():  # Return True if the client is in Controlled state
            if x <= self.threshold:
                self.send(None, format_command(f"return left {y / self.screen_height}"))
                self.client_status.set_state(HiddleState())
            elif x >= self.screen_width - self.threshold:
                self.send(None, format_command(f"return right {y / self.screen_height}"))
                self.client_status.set_state(HiddleState())
            elif y <= self.threshold:
                self.send(None, format_command(f"return up {x / self.screen_width}"))
                self.client_status.set_state(HiddleState())
            elif y >= self.screen_height - self.threshold:
                self.send(None, format_command(f"return down {x / self.screen_width}"))
                self.client_status.set_state(HiddleState())

        return True

    def __str__(self):
        return "ClientMouseListener"


class ClientKeyboardListener(HandlerInterface):
    def __init__(self):
        self.keyboard = KeyboardController()
        self.send = QueueManager(None).send_keyboard
        self.logger = Logger.get_instance().log

        self.file_transfer_handler = FileTransferEventHandler()
        self.command_pressed = False

        self._listener = KeyboardListener(on_press=self.on_press, on_release=self.on_release)

    def get_listener(self):
        return self._listener

    def start(self):
        self.logger("Starting keyboard listener")
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
                self.file_transfer_handler.handle_file_paste(current_dir, self.file_transfer_handler.CLIENT_REQUEST)

    def on_release(self, key: Key | KeyCode | None):

        # Check if command is released
        if key == Key.cmd:
            self.command_pressed = False
