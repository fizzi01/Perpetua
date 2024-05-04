import socket
from collections.abc import Callable

from pynput import mouse, keyboard
from pynput.keyboard import Key, KeyCode


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

        self._listener = mouse.Listener(on_move=self.on_move, on_scroll=self.on_scroll, on_click=self.on_click,
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
            try:
                self.send(screen, f"mouse move {x} {y}\n")
            except socket.error as e:
                print(f"Error {e}")
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

        self._listener = keyboard.Listener(on_press=self.on_press, on_release=self.on_release,
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
