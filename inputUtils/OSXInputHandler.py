import socket
from collections.abc import Callable

from pynput import mouse


class ServerMouseListener:

    def __init__(self, send_function: Callable, change_screen_function: Callable, get_active_screen: Callable,
                 get_clients: Callable, screen_width: int, screen_height: int, lock):
        self.send = send_function
        self.active_screen = get_active_screen
        self.change_screen = change_screen_function
        self.clients = get_clients
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.lock = lock
        self._listener = mouse.Listener(on_move=self.on_move, on_scroll=self.on_scroll, on_click=self.on_click,
                                        suppress=False,
                                        darwin_event_filter=self.mouse_suppress_filter)

    def get_listener(self):
        return self._listener

    def mouse_suppress_filter(self, msg, data):

        screen = self.active_screen()
        listener = self._listener

        if screen and (msg == 513 or msg == 514):  # Left click
            print("Suppressing Mouse click")
            listener._suppress = True
        elif screen and (msg == 516 or msg == 517):  # Right click
            print("Suppressing Mouse click")
            listener._suppress = True
        elif screen and (msg == 519 or msg == 520):  # Middle click
            print("Suppressing Mouse click")
            listener._suppress = True
        elif screen and (msg == 522 or msg == 523):  # Scroll
            print("Suppressing Mouse scroll")
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
            if x >= self.screen_width - 10:  # Soglia per passare al monitor a destra
                self.change_screen(None)
            elif x <= 10:  # Soglia per passare al monitor a sinistra
                self.change_screen("left")
            elif y >= self.screen_height - 10:  # Soglia per passare al monitor sopra
                return True
            elif y <= 10:  # Soglia per passare al monitor sotto
                return True

        return True

    def on_click(self, x, y, button, pressed):
        screen = self.active_screen()
        clients = self.clients(screen)

        if button == mouse.Button.left:
            if screen and clients and pressed:
                self.send(screen, f"mouse click {x} {y}\n")
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
    def __init__(self):
        pass


class ServerClipboardListener:
    def __init__(self):
        pass