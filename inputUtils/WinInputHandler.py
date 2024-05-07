import threading
import time
from collections.abc import Callable
import pyperclip

from pynput import mouse, keyboard
from pynput.keyboard import Key, KeyCode, Listener as KeyboardListener, Controller as KeyboardController
import keyboard
from pynput.mouse import Button, Controller as MouseController, Listener as MouseListener

from utils.netData import *


class ServerMouseListener:
    """
    :param send_function: Function to send data to the clients
    :param change_screen_function: Function to change the active screen
    :param get_active_screen: Function to get the active screen
    :param get_clients: Function to get the clients of the current screen
    :param screen_width: Width of the screen
    :param screen_height: Height of the screen
    :param screen_threshold: Threshold to change the screen
    """

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
                                       win32_event_filter=self.mouse_suppress_filter)

    def get_listener(self):
        return self._listener

    def start(self):
        self._listener.start()

    def stop(self):
        if self.is_alive():
            self._listener.stop()

    def is_alive(self):
        return self._listener.is_alive()

    def mouse_suppress_filter(self, msg, data):

        screen = self.active_screen()
        listener = self._listener

        if screen and (msg == 513 or msg == 514):  # Left click
            listener._suppress = True
        elif screen and (msg == 516 or msg == 517):  # Right click
            listener._suppress = True
        elif screen and (msg == 519 or msg == 520):  # Middle click
            listener._suppress = True
        elif screen and (msg == 522 or msg == 523):  # Scroll
            listener._suppress = True
        else:
            listener._suppress = False

        return True

    def on_move(self, x, y):

        screen = self.active_screen()
        clients = self.clients(screen)
        is_transmitting = self.get_trasmission_status()

        normalized_x = x / self.screen_width
        normalized_y = y / self.screen_height

        if screen and clients and is_transmitting:
            command = format_command(f"mouse move {normalized_x} {normalized_y}")
            self.send(screen, command)
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
                                          win32_event_filter=self.keyboard_suppress_filter)

    def get_listener(self):
        return self._listener

    def start(self):
        self._listener.start()

    def stop(self):
        if self.is_alive():
            self._listener.stop()

    def is_alive(self):
        return self._listener.is_alive()

    def keyboard_suppress_filter(self, msg, data):
        screen = self.active_screen()
        listener = self._listener

        if screen:
            listener._suppress = True
        else:
            listener._suppress = False

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
        self.active_clients = get_clients
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
        self.key_filter = {
            "option": "alt",
            "option_r": "alt_gr",
            "alt_r": "alt_gr",
        }
        self.special_keys = ["{", "}", "[", "]", "@", "#", "~", "«", "»", "€", "£", "“", "”", "’", "‘", "´", "`", "^",
                             "¨", "‹", "÷", "≠", "¡", "ˆ", "¥"]
        self.controller = KeyboardController()
        self.caps_lock_state = False

    def data_filter(self, key_data):
        if key_data in self.key_filter:
            return self.key_filter[key_data]
        return key_data

    @staticmethod
    def get_key(key_data: str):
        try:
            key = Key[key_data]
            return key
        except Exception:
            return key_data

    def is_special_key(self, key_data: str | Key):
        if isinstance(key_data, Key):
            return Key.name in self.special_keys
        else:
            return key_data in self.special_keys

    def process_key_command(self, key_data, key_action):
        key_data = self.data_filter(key_data)

        if key_action == "press":
            if key_data is Key.caps_lock:
                if self.caps_lock_state:
                    self.controller.release(key_data)
                    self.caps_lock_state = False
                else:
                    self.controller.press(key_data)
                    self.caps_lock_state = True
            else:
                if self.is_special_key(key_data):  # Special key handler
                    self.controller.release(Key.alt_gr)
                    self.controller.release(Key.alt)

                self.controller.press(self.get_key(key_data))
        elif key_action == "release":
            self.controller.release(self.get_key(key_data))


class ClientMouseController:
    def __init__(self, screen_width, screen_height):
        self.mouse = MouseController()
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.pressed = False
        self.last_press_time = -99
        self.doubleclick_counter = 0

    def smooth_move(self, start_x, start_y, end_x, end_y, steps=6, sleep_time=0.0006):
        # Calcola la differenza tra la posizione iniziale e finale
        dx = end_x - start_x
        dy = end_y - start_y

        # Calcola il passo per ogni dimensione
        x_step = dx / steps
        y_step = dy / steps

        # Muovi il mouse attraverso i passaggi
        for i in range(steps):
            new_x = start_x + i * x_step
            new_y = start_y + i * y_step
            self.mouse.position = (new_x, new_y)
            time.sleep(sleep_time)

        # Assicurati che il mouse sia esattamente nella posizione finale
        self.mouse.position = (end_x, end_y)

    def process_mouse_command(self, x, y, mouse_action, is_pressed):
        if mouse_action == "move":
            target_x = max(0, min(x * self.screen_width, self.screen_width))  # Ensure target_x is within screen bounds
            target_y = max(0,
                           min(y * self.screen_height, self.screen_height))  # Ensure target_y is within screen bounds

            current_x, current_y = self.mouse.position
            self.smooth_move(current_x, current_y, target_x, target_y)

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

    def smooth_scroll(self, x, y, delay=0.01, steps=2):
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
