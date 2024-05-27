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

from utils.netData import *


class ServerMouseListener:

    def __init__(self, send_function: Callable, change_screen_function: Callable, get_active_screen: Callable,
                 get_status: Callable,
                 get_clients: Callable, screen_width: int, screen_height: int, screen_threshold: int = 5):
        self.send = send_function
        self.active_screen = get_active_screen
        self.change_screen = change_screen_function
        self.get_trasmission_status = get_status
        self.clients = get_clients
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.screen_treshold = screen_threshold
        self._listener = MouseListener(on_move=self.on_move, on_scroll=self.on_scroll, on_click=self.on_click,
                                       darwin_intercept=self.mouse_suppress_filter)

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
            if event_type == Quartz.kCGEventLeftMouseDown:
                print("Trackpad left click")
            elif event_type == Quartz.kCGEventRightMouseDown:
                print("Trackpad right click")
            elif event_type == Quartz.kCGEventOtherMouseDown:
                print("Other trackpad action")
            elif event_type == Quartz.kCGEventLeftMouseDragged:
                print("Trackpad left drag")
            elif event_type == Quartz.kCGEventRightMouseDragged:
                print("Trackpad right drag")
            elif event_type == Quartz.kCGEventOtherMouseDragged:
                print("Other trackpad drag")
            elif event_type == Quartz.kCGEventScrollWheel:
                print("Scroll wheel")
            else:
                return event
        else:
            return event

    def on_move(self, x, y):

        screen = self.active_screen()
        clients = self.clients(screen)
        is_transmitting = self.get_trasmission_status()

        normalized_x = x / self.screen_width
        normalized_y = y / self.screen_height

        if screen and clients and is_transmitting:
            self.send(screen, format_command(f"mouse move {normalized_x} {normalized_y}"))
        else:
            if x >= self.screen_width - self.screen_treshold:  # Soglia per passare al monitor a destra
                self.change_screen("right")
            elif x <= self.screen_treshold:  # Soglia per passare al monitor a sinistra
                self.change_screen("left")
            elif y >= self.screen_height - self.screen_treshold:  # Soglia per passare al monitor sopra
                self.change_screen("down")
            elif y <= self.screen_treshold:  # Soglia per passare al monitor sotto
                self.change_screen("up")

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


class ServerKeyboardListener:
    """
    :param send_function: Function to send data to the clients
    :param get_clients: Function to get the clients of the current screen
    :param get_active_screen: Function to get the active screen
    """

    def __init__(self, send_function: Callable, get_clients: Callable, get_active_screen: Callable):
        self.clients = get_clients
        self.active_screen = get_active_screen
        self.send = send_function

        self._listener = KeyboardListener(on_press=self.on_press, on_release=self.on_release,
                                          darwin_intercept=self.keyboard_suppress_filter)

        self._caps_lock = False

    def get_listener(self):
        return self._listener

    def start(self):
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
                print("Caps Lock is pressed")
                return event
            elif event_type == Quartz.kCGEventKeyDown:  # Key press event
                print(f"Key pressed blocked: {event}")
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


class ServerClipboardListener:
    def __init__(self, send_function: Callable, get_clients: Callable, get_active_screen: Callable):

        self.send = send_function
        self.clients = get_clients
        self.active_screen = get_active_screen
        self._thread = None
        self.last_clipboard_content = pyperclip.paste()  # Inizializza con il contenuto attuale della clipboard
        self._stop_event = threading.Event()

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


class ClientClipboardListener:
    def __init__(self, send_func: Callable):

        self.send = send_func
        self._thread = None
        self.last_clipboard_content = pyperclip.paste()  # Inizializza con il contenuto attuale della clipboard
        self._stop_event = threading.Event()

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
                self.send(format_command("clipboard ") + current_clipboard_content)
                self.last_clipboard_content = current_clipboard_content
            time.sleep(0.5)


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


class ClientMouseController:
    def __init__(self, screen_width, screen_height):
        self.mouse = MouseController()
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.pressed = False
        self.last_press_time = -99
        self.doubleclick_counter = 0

    def process_mouse_command(self, x, y, mouse_action, is_pressed):
        if mouse_action == "move":
            target_x = max(0, min(x * self.screen_width, self.screen_width))  # Ensure target_x is within screen bounds
            target_y = max(0,
                           min(y * self.screen_height, self.screen_height))  # Ensure target_y is within screen bounds

            self.mouse.position = (target_x, target_y)

        elif mouse_action == "click":
            self.handle_click(Button.left, is_pressed)
        elif mouse_action == "right_click":
            self.mouse.click(Button.right)
        elif mouse_action == "scroll":
            # High performance impact without threading
            threading.Thread(target=self.smooth_scroll, args=(x, y)).start()

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


class ClientMouseListener:
    def __init__(self, screen_width, screen_height, threshold, send_func: Callable, client_socket=None):
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.threshold = threshold
        self.send = send_func
        self.client_socket = client_socket
        self._listener = MouseListener(on_move=self.handle_mouse)

    def get_listener(self):
        return self._listener

    def start(self):
        self._listener.start()

    def stop(self):
        self._listener.stop()

    def handle_mouse(self, x, y):

        if x <= self.threshold:
            self.send(format_command(f"return left {y / self.screen_height}"))
        elif x >= self.screen_width - self.threshold:
            self.send(format_command(f"return right {y / self.screen_height}"))
