import socket
import threading
import time
from collections.abc import Callable

from inputUtils import InputHandler as inputHandler
from .ServerHandler import ServerHandler, ServerCommandProcessor
from utils import screen_size
from utils.netData import *


class Client:
    def __init__(self, server: str, port: int, threshold: int = 10,
                 wait: int = 5,
                 logging: bool = False, stdout: Callable = print, root=None):

        self.server = server
        self.port = port
        self.screen_width, self.screen_height = screen_size()
        self.threshold = threshold
        self.wait = wait
        self.logging = logging
        self.stdout = stdout

        self.client_socket = None

        self.lock = threading.Lock()
        self._running = False
        self._connected = None

        self._client_thread = None
        self._is_client_thread_running = False

        self._connection_thread = None

        self.window = None  # TODO: Window(root) screen transition
        self.stdout = stdout

        self.on_screen = False  # TODO
        self.changed = False  # TODO
        self.transition_handler = None  # Thread che controlla on_screen, se False chiama self.window.show()
        # Il server invierà un comando per avvertire il client che non è più on_screen

        self.keyboard_controller = inputHandler.ClientKeyboardController()
        self.mouse_controller = inputHandler.ClientMouseController(self.screen_width, self.screen_height)

        self.mouse_listener = inputHandler.ClientMouseListener(screen_width=self.screen_width,
                                                               screen_height=self.screen_height,
                                                               send_func=self._send_to,
                                                               client_socket=self.client_socket,
                                                               threshold=self.threshold)

        self.clipboard_listener = inputHandler.ClientClipboardListener(send_func=self._send_to)

        self.processor = ServerCommandProcessor(self.on_screen, self.mouse_controller,
                                                self.keyboard_controller, self.clipboard_listener)


    def start(self):
        try:
            if not self._running:
                self._running = True
                self._client_thread = threading.Thread(target=self._run, daemon=True)
                self._client_thread.start()
                self._is_client_thread_running = True
                self._start_listeners()
                return True
            else:
                self.log("Client already running.", 1)
                return False

        except Exception as e:
            self.log(f"{e}", 2)
            self._running = False
            return False

    def _start_listeners(self):
        try:
            self.mouse_listener.start()
            self.clipboard_listener.start()
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

                    try:  # Handle connection timeout
                        self.client_socket.connect((self.server, self.port))
                    except socket.error as e:
                        self.log(f"{e}", 2)
                        continue

                    self._connected = True
                    self.log("Connected to the server.", 1)

                    self._connection_thread = ServerHandler(connection=self.client_socket,
                                                            command_func=self.processor.process_command,
                                                            on_disconnect=self.on_disconnect, logger=self.log)
                    self._connection_thread.start()
                else:
                    self._is_client_thread_running = False
                    break
            time.sleep(0.2)
        except Exception as e:
            self.log(f"Error connecting to the server: {e}", 2)
            self._is_client_thread_running = False

        self.log("Client stopped.", 1)

    def stop(self):
        if self._running and self._is_client_thread_running:
            try:

                if self._connection_thread:
                    self._connection_thread.stop()

                self.mouse_listener.stop()
                self._running = False

                if self._check_main_thread():
                    self.log(f"Server stopped.", 1)
                    return True

                self.log("Client stopped.", 1)
                return True
            except Exception as e:
                self.log(f"{e}", 2)
                return False
        else:
            return True

    def _check_main_thread(self):
        max_retry = 5
        retry = 0
        while self._is_client_thread_running and retry < max_retry:
            time.sleep(0.5)
            retry += 1

        if retry < max_retry:
            return True
        else:
            if self._is_client_thread_running:
                raise Exception("Thread principale non terminata.")

    def _send_to(self, command):

        command = format_data(command)

        if self.client_socket:
            try:
                # Split the command into chunks if it's too long
                if len(command) > CHUNK_SIZE - len(END_DELIMITER):
                    chunks = [command[i:i + CHUNK_SIZE] for i in range(0, len(command), CHUNK_SIZE)]
                    for i, chunk in enumerate(chunks):
                        if i == len(chunks) - 1 and len(chunk) > CHUNK_SIZE - len(
                                END_DELIMITER):  # Check if this is the last chunk and its length is > CHUNK_SIZE - END_DELIMITER
                            self.client_socket.send(chunk.encode())  # Send the last chunk without any terminator
                            self.client_socket.send(END_DELIMITER.encode())  # Send the terminator as a separate chunk
                        elif i == len(
                                chunks) - 1:  # This is the last chunk and its length is less than CHUNK_SIZE - END_DELIMITER
                            chunk = chunk + END_DELIMITER
                            self.client_socket.send(chunk.encode())
                        else:
                            chunk = chunk + CHUNK_DELIMITER
                            self.client_socket.send(chunk.encode())
                else:
                    command = command + END_DELIMITER
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
            print(f"{msg}")
        if priority == 1:
            self.stdout(f"[INFO] {msg}")
            print(f"[INFO] {msg}")
        if priority == 2:
            self.stdout(f"[ERROR] {msg}")
            print(f"[ERROR] {msg}")
