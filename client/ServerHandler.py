import socket
import threading
from collections.abc import Callable
from time import sleep

from utils.netData import *


class ServerHandler:
    def __init__(self, connection: socket, command_func: Callable, on_disconnect: Callable, logger: Callable):
        self.buffer = ""
        self.conn = connection
        self.process_command = command_func
        self.on_disconnect = on_disconnect
        self.log = logger
        self._running = False
        self.thread = None

    def start(self):
        self._running = True
        self.thread = threading.Thread(target=self.handle_server_commands)
        try:
            self.thread.start()
            self.log("Client in listening mode.")
        except Exception as e:
            self.log(f"Error starting client: {e}", 2)

    def stop(self):
        self._running = False
        self.conn.close()
        self.on_disconnect()
        self.log("Client disconnected.", 1)

    def handle_server_commands(self):
        """Handle commands continuously received from the server."""
        temp_buffer = ""
        while self._running:
            try:
                data = self.conn.recv(1024).decode()
                if not data:
                    break
                self.buffer += data

                while END_DELIMITER in self.buffer or CHUNK_DELIMITER in self.buffer:
                    if END_DELIMITER in self.buffer:
                        # Find the first end delimiter
                        pos = self.buffer.find(END_DELIMITER)
                        # Extract the complete command
                        command = temp_buffer + self.buffer[:pos]
                        temp_buffer = ""  # Clear the temporary buffer

                        # Remove the command from the buffer
                        self.buffer = self.buffer[pos + len(END_DELIMITER):]  # Skip the length of END_DELIMITER
                        # Process the command
                        threading.Thread(target=self.process_command, args=(command,)).start()
                    elif CHUNK_DELIMITER in self.buffer:
                        # Find the first message end delimiter
                        pos = self.buffer.find(CHUNK_DELIMITER)
                        # Add the chunk to the temporary buffer
                        temp_buffer += self.buffer[:pos]

                        # Remove the chunk from the buffer
                        self.buffer = self.buffer[pos + len(CHUNK_DELIMITER):]  # Skip the length of CHUNK_DELIMITER
                    sleep(0.000001)
            except Exception as e:
                self.log(f"Error receiving data: {e}", 2)
                break
        self.stop()


class ServerCommandProcessor:
    def __init__(self, on_screen_func, mouse_controller, keyboard_controller, clipboard):
        self.on_screen = on_screen_func
        self.mouse_controller = mouse_controller
        self.keyboard_controller = keyboard_controller
        self.clipboard = clipboard

    def process_command(self, command):
        parts = extract_command_parts(command)
        if parts[0] == "mouse":
            x, y = float(parts[2]), float(parts[3])
            event = parts[1]
            is_pressed = parts[4] == "true" if len(parts) > 4 else False
            self.mouse_controller.process_mouse_command(x, y, event, is_pressed)
        elif parts[0] == "keyboard":
            key, event = parts[2], parts[1]
            self.keyboard_controller.process_key_command(key, event)
        elif parts[0] == "clipboard":
            content = extract_text(parts[1])
            self.clipboard.set_clipboard(content)
        elif parts[0] == "screen":  # Update screen status
            is_on_screen = parts[1] == "true"
            self.on_screen(is_on_screen)  # TODO
