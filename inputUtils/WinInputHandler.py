import threading
import time
from collections.abc import Callable

from pynput import mouse, keyboard
from pynput.keyboard import Key, KeyCode, Listener as KeyboardListener
import keyboard
from pynput.mouse import Button, Controller as MouseController, Listener as MouseListener


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
                 get_clients: Callable, screen_width: int, screen_height: int, screen_threshold: int = 5):
        self.send = send_function
        self.active_screen = get_active_screen
        self.change_screen = change_screen_function
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
        self._listener.stop()

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

        if screen and clients:
            self.send(screen, f"mouse move {x} {y}\n")
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
                self.send(screen, f"mouse click {x} {y} true\n")
            elif screen and clients and not pressed:
                self.send(screen, f"mouse click {x} {y} false\n")
        elif button == mouse.Button.right:
            if screen and clients and pressed:
                self.send(screen, f"mouse right_click {x} {y}\n")
        elif button == mouse.Button.middle:
            if screen and clients and pressed:
                self.send(screen, f"mouse middle_click {x} {y}\n")
        return True

    def on_scroll(self, x, y, dx, dy):
        screen = self.active_screen()
        clients = self.clients(screen)
        if screen and clients:
            self.send(screen, f"mouse scroll {dx} {dy}\n")
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
        self._listener.stop()

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
            self.send(screen, f"keyboard press {data}\n")

    def on_release(self, key: Key | KeyCode | None):
        screen = self.active_screen()
        clients = self.clients(screen)

        if isinstance(key, Key):
            data = key.name
        else:
            data = key.char

        if screen and clients:
            self.send(screen, f"keyboard release {data}\n")


class ServerClipboardListener:
    def __init__(self):
        pass


class ClientKeyboardController:
    def __init__(self):
        self.pressed_keys = set()
        self.key_filter = {
            ",": "comma",
            "+": "plus"
        }

    @staticmethod
    def _key_cleanup(key_data):
        if key_data.endswith("_l"):
            key_data = key_data[:-2]
        elif key_data.endswith("_r"):
            key_data = key_data[:-2]
        elif key_data.endswith("_gr"):
            key_data = key_data[:-3]
        return key_data

    def data_filter(self, key_data):
        if key_data in self.key_filter:
            return self.key_filter[key_data]
        return key_data

    def process_key_command(self, key_data, key_action):
        key_data = self._key_cleanup(key_data)
        key_data = self.data_filter(key_data)

        if key_action == "press":
            if keyboard.is_modifier(key_data):
                if key_data not in self.pressed_keys:
                    keyboard.press(key_data)
                    if key_data not in ["backspace", "tab", "delete", "enter", "space"]:
                        self.pressed_keys.add(key_data)
            else:
                if len(self.pressed_keys) != 0:
                    pressed_list = list(self.pressed_keys)
                    pressed_list.append(key_data)
                    hotkey = pressed_list
                    keyboard.press(hotkey)
                else:
                    hotkey = key_data
                    keyboard.press(hotkey)
        elif key_action == "release":
            try:
                if keyboard.is_modifier(key_data):
                    keyboard.release(key_data)
                    self.pressed_keys.remove(key_data)
                else:
                    keyboard.release(key_data)
            except IndexError:
                pass


class ClientMouseController:
    def __init__(self):
        self.mouse = MouseController()
        self.pressed = False
        self.last_press_time = -99
        self.doubleclick_counter = 0

    def process_mouse_command(self, x, y, mouse_action, is_pressed):
        if mouse_action == "move":
            self.mouse.position = (x, y)
        elif mouse_action == "click":
            self.handle_click(x, y, Button.left, is_pressed)
        elif mouse_action == "right_click":
            self.mouse.click(Button.right)
        elif mouse_action == "scroll":
            # High performance impact without threading
            threading.Thread(target=self.smooth_scroll, args=(x, y)).start()

    def handle_click(self, x, y, button, is_pressed):
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
            self.send(f"return left {y}\n")
        elif x >= self.screen_width - self.threshold:
            self.send(f"return right {y}\n")
