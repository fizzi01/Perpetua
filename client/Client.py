import socket
import threading
import time
from collections.abc import Callable

from inputUtils import InputHandler as inputHandler
from .ServerHandler import ServerHandler, ServerCommandProcessor


class Client:
    def __init__(self, server: str, port: int, screen_width: int = 1920, screen_height: int = 1080, threshold: int = 10,
                 wait: int = 5,
                 logging: bool = False, stdout: Callable = print, root=None):

        self.server = server
        self.port = port
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.threshold = threshold
        self.wait = wait
        self.logging = logging
        self.stdout = stdout

        self.client_socket = None

        self.lock = threading.Lock()
        self._running = False
        self._connected = None
        self._client_thread = None

        self.window = None  # Window(root)
        self.stdout = stdout

        self.on_screen = False
        self.changed = False
        self.transition_handler = None  # Thread che controlla on_screen, se False chiama self.window.show()
        # Il server invierà un comando per avvertire il client che non è più on_screen

        self.processor = None

        self.keyboard_controller = inputHandler.ClientKeyboardController()
        self.mouse_controller = inputHandler.ClientMouseController()

        self.mouse_listener = None
        self.clipboard_listener = None

    def start(self):
        try:
            if not self._running:
                self._running = True
                self._client_thread = threading.Thread(target=self._run)
                self._client_thread.start()
                self._start_listeners()
                return True
            else:
                self.log("Client already running.", 1)
                return False
        except Exception as e:
            self.log(f"{e}", 2)
            return False

    def _start_listeners(self):
        self.mouse_listener = inputHandler.ClientMouseListener(screen_width=self.screen_width,
                                                               screen_height=self.screen_height,
                                                               send_func=self._send_to,
                                                               client_socket=self.client_socket,
                                                               threshold=self.threshold)
        try:
            self.mouse_listener.start()
        except Exception as e:
            self.log(f"Error starting mouse listener: {e}")
            self.stop()

    def _run(self):
        try:
            while True:
                if self._running:

                    if self._connected:
                        time.sleep(self.wait)
                        continue

                    self.client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    self.client_socket.connect((self.server, self.port))
                    self._connected = True
                    self.log("Connected to the server.", 1)
                    self.processor = ServerCommandProcessor(self.on_screen, self.mouse_controller, self.keyboard_controller, None)
                    handler = ServerHandler(connection=self.client_socket, command_func=self.processor.process_command, on_disconnect=self.on_disconnect,logger=self.log)
                    handler.start()
                    self._client_thread = handler
                else:
                    break
        except Exception as e:
            self.log(f"Error connecting to the server: {e}", 2)

    def stop(self):
        self._running = False
        try:
            self._client_thread.stop()
            self.mouse_listener.stop()
            self.log("Client stopped.", 1)
        except Exception as e:
            self.log(f"Error stopping client: {e}", 2)

    def _send_to(self, command):
        if self.client_socket:
            try:
                self.client_socket.send(command.encode())
            except Exception as e:
                self.log(f"Error sending data: {e}", 2)
                self.stop()

    def on_disconnect(self):
        self._connected = False

    def on_screen(self, is_on_screen: bool):
        self.on_screen = is_on_screen
        with self.lock:
            self.changed = True

    def screen_transition_handler(self):
        while self._running:
            if self.changed:
                if not self.on_screen:
                    self.window.show()
                else:
                    self.window.hide()
                self.changed = False
            time.sleep(0.1)

    def log(self, msg, priority=0):
        if priority == 0 and self.logging:
            self.stdout(f"{msg}")
        if priority == 1:
            self.stdout(f"[INFO] {msg}")
        if priority == 2:
            self.stdout(f"[ERROR] {msg}")
