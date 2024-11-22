import math
from collections.abc import Callable

import pyperclip
from pynput import mouse
import Quartz

import threading
import time

import keyboard as hotkey_controller
from pynput.mouse import Button, Controller as MouseController
from pynput.mouse import Listener as MouseListener
from pynput.keyboard import Listener as KeyboardListener, Key, KeyCode, Controller as KeyboardController

from utils.Logging import Logger
from utils.netData import *

from network.IOManager import QueueManager


class ServerMouseController:
    pass


class ServerMouseListener:
    IGNORE_NEXT_MOVE_EVENT = 0.009
    MAX_DXDY_THRESHOLD = 150

    def __init__(self, change_screen_function: Callable, get_active_screen: Callable,
                 get_status: Callable,
                 get_clients: Callable, screen_width: int, screen_height: int, screen_threshold: int = 5):

        self.ignore_move_events_until = 0

        self.active_screen = get_active_screen
        self.change_screen = change_screen_function
        self.get_trasmission_status = get_status
        self.clients = get_clients
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.screen_treshold = screen_threshold

        self.logger = Logger.get_instance().log
        self.send = QueueManager(None).send_mouse

        self.last_x = None
        self.last_y = None
        self.mouse_controller = MouseController()

        self._listener = MouseListener(on_move=self.on_move, on_scroll=self.on_scroll, on_click=self.on_click,
                                       darwin_intercept=self.mouse_suppress_filter)

        self.move_threshold = 2  # Minimum movement required to trigger on_move

    def get_listener(self):
        return self._listener

    def start(self):
        self.logger("Starting mouse listener")
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
            if event_type in [
                Quartz.kCGEventLeftMouseDown,
                Quartz.kCGEventRightMouseDown,
                Quartz.kCGEventOtherMouseDown,
                Quartz.kCGEventLeftMouseDragged,
                Quartz.kCGEventRightMouseDragged,
                Quartz.kCGEventOtherMouseDragged,
                Quartz.kCGEventScrollWheel
            ]:
                pass
            else:
                return event
        else:
            return event

    def warp_cursor_to_center(self):
        self.ignore_move_events_until = time.time() + self.IGNORE_NEXT_MOVE_EVENT
        center_x = self.screen_width // 2
        center_y = self.screen_height // 2
        self.mouse_controller.position = (center_x, center_y)
        self.last_x, self.last_y = self.mouse_controller.position

    def on_move(self, x, y):
        current_time = time.time()

        if self.ignore_move_events_until > current_time:
            self.last_x = x
            self.last_y = y
            return True

        # Calcola il movimento relativo
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

        # Aggiorna l'ultima posizione e tempo conosciuti
        self.last_x = x
        self.last_y = y

        # Controlla se il cursore è al bordo
        at_right_edge = x >= self.screen_width - 1
        at_left_edge = x <= 0
        at_bottom_edge = y >= self.screen_height - 1
        at_top_edge = y <= 0

        screen = self.active_screen()
        clients = self.clients(screen)
        is_transmitting = self.get_trasmission_status()

        if screen and clients and is_transmitting:
            scale_x = 1920 / self.screen_width
            scale_y = 1080 / self.screen_height
            dx *= scale_x
            dy *= scale_y
            self.logger(f"Mouse moved: {dx}, {dy}", Logger.WARNING)
            self.send(screen, format_command(f"mouse move {dx} {dy}"))
            self.warp_cursor_to_center()
        else:
            # Quando si attraversa un bordo, invia una posizione assoluta normalizzata
            if at_right_edge:
                self.change_screen("right")
                normalized_x = 0.05  # Entra dal bordo sinistro del client
                normalized_y = y / self.screen_height
                self.send("right", format_command(f"mouse position {normalized_x} {normalized_y}"))
            elif at_left_edge:
                self.change_screen("left")
                normalized_x = 0.95  # Entra dal bordo destro del client
                normalized_y = y / self.screen_height
                self.send("left", format_command(f"mouse position {normalized_x} {normalized_y}"))
            elif at_bottom_edge:
                self.change_screen("down")
                normalized_x = x / self.screen_width
                normalized_y = 0.05  # Entra dal bordo superiore del client
                self.send("down", format_command(f"mouse position {normalized_x} {normalized_y}"))
            elif at_top_edge:
                self.change_screen("up")
                normalized_x = x / self.screen_width
                normalized_y = 0.95  # Entra dal bordo inferiore del client
                self.send("up", format_command(f"mouse position {normalized_x} {normalized_y}"))

        return True

    def on_click(self, x, y, button, pressed):
        screen = self.active_screen()
        clients = self.clients(screen)

        if button == mouse.Button.left:
            if screen and clients and pressed:
                self.send(screen, format_command(f"mouse click {x} {y} true"))
            elif screen and clients and not pressed:
                self.send(screen, format_command(f"mouse click {x} {y} false"))
        elif button == mouse.Button.right:
            if screen and clients and pressed:
                self.send(screen, format_command(f"mouse right_click {x} {y}"))
        elif button == mouse.Button.middle:
            if screen and clients and pressed:
                self.send(screen, format_command(f"mouse middle_click {x} {y}"))
        return True

    def on_scroll(self, x, y, dx, dy):
        screen = self.active_screen()
        clients = self.clients(screen)
        if screen and clients:
            self.send(screen, format_command(f"mouse scroll {dx} {dy}"))
        return True

    def __str__(self):
        return "ServerMouseListener"


class ServerKeyboardListener:
    """
    :param send_function: Function to send data to the clients
    :param get_clients: Function to get the clients of the current screen
    :param get_active_screen: Function to get the active screen
    """

    def __init__(self, get_clients: Callable, get_active_screen: Callable):
        self.clients = get_clients
        self.active_screen = get_active_screen
        self.send = QueueManager(None).send_keyboard
        self.logger = Logger.get_instance().log

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

    def keyboard_suppress_filter(self, event_type, event):
        screen = self.active_screen()

        flags = Quartz.CGEventGetFlags(event)
        caps_lock = flags & Quartz.kCGEventFlagMaskAlphaShift

        if screen:
            if caps_lock != 0:
                self.logger("Caps Lock is pressed")
                return event
            elif event_type == Quartz.kCGEventKeyDown:  # Key press event
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

        if screen and clients:
            self.send(screen, format_command(f"keyboard press {data}"))

    def on_release(self, key: Key | KeyCode | None):
        screen = self.active_screen()
        clients = self.clients(screen)

        if isinstance(key, Key):
            data = key.name
        else:
            data = key.char

        if screen and clients:
            self.send(screen, format_command(f"keyboard release {data}"))

    def __str__(self):
        return "ServerKeyboardListener"


class ServerClipboardListener:
    def __init__(self, get_clients: Callable, get_active_screen: Callable):

        self.send = QueueManager(None).send_clipboard

        self.clients = get_clients
        self.active_screen = get_active_screen
        self._thread = None
        self.last_clipboard_content = pyperclip.paste()  # Inizializza con il contenuto attuale della clipboard
        self._stop_event = threading.Event()
        self.logger = Logger.get_instance().log

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

    def _run(self):
        while not self._stop_event.is_set():
            current_clipboard_content = pyperclip.paste()
            if current_clipboard_content != self.last_clipboard_content:
                self.send("all", format_command("clipboard ") + current_clipboard_content)
                self.last_clipboard_content = current_clipboard_content
            time.sleep(0.5)

    def __str__(self):
        return "ServerClipboardListener"


class ClientClipboardListener:
    def __init__(self):

        self.send = QueueManager(None).send_clipboard
        self._thread = None
        self.last_clipboard_content = pyperclip.paste()  # Inizializza con il contenuto attuale della clipboard
        self._stop_event = threading.Event()
        self.logger = Logger.get_instance().log

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

    def _run(self):
        while not self._stop_event.is_set():
            current_clipboard_content = pyperclip.paste()
            if current_clipboard_content != self.last_clipboard_content:
                self.send(None, format_command("clipboard ") + current_clipboard_content)
                self.last_clipboard_content = current_clipboard_content
            time.sleep(0.5)

    def __str__(self):
        return "ClientClipboardListener"


class ClientKeyboardController:
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
    def is_shift(key_data):
        return key_data == "shift"

    @staticmethod
    def is_alt(key_data):
        return key_data == "alt_l"

    def process_key_command(self, key_data, key_action):
        key_data = self.data_filter(key_data)

        if key_action == "press":
            print(key_data)
            if self.is_alt_gr(key_data):
                self.keyboard.release(Key.ctrl_r)
            if self.is_shift(key_data) or self.is_alt(key_data):
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
            if self.is_shift(key_data) or self.is_alt(key_data) and len(self.pressed_keys) > 0:
                self.pressed_keys.remove(key_data)
            self.keyboard.release(self.get_key(key_data))

    def __str__(self):
        return "ClientKeyboardController"


class ClientMouseController:
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

        self.log = Logger.get_instance().log

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
            self.mouse.move(x, y)
        elif mouse_action == "click":
            self.handle_click(Button.left, is_pressed)
        elif mouse_action == "right_click":
            self.mouse.click(Button.right)
        elif mouse_action == "scroll":
            self.smooth_scroll(x, y)  # Fall back without threading

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


class ClientMouseListener:
    def __init__(self, screen_width, screen_height, threshold):
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.threshold = threshold
        self.send = QueueManager(None).send_mouse
        self._listener = MouseListener(on_move=self.handle_mouse)
        self.logger = Logger.get_instance().log

    def get_listener(self):
        return self._listener

    def start(self):
        self._listener.start()

    def stop(self):
        self._listener.stop()

    def handle_mouse(self, x, y):
        if abs(x) <= self.threshold:
            self.send(None, format_command(f"return left {y / self.screen_height}"))
        elif abs(x) >= self.screen_width - self.threshold:
            self.send(None, format_command(f"return right {y / self.screen_height}"))
        elif abs(y) <= self.threshold:
            self.send(None, format_command(f"return up {x / self.screen_width}"))
        elif abs(y) >= self.screen_height - self.threshold:
            self.send(None, format_command(f"return down {x / self.screen_width}"))

        return True

    def __str__(self):
        return "ClientMouseListener"
