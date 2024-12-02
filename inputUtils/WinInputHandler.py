import math
import threading
import time
import urllib
from collections.abc import Callable

# System libraries
import pythoncom
import win32clipboard
import win32con
import win32gui
import win32process
from comtypes import client
from comtypes import stream
import psutil
import os

# Input handling libraries
import pyperclip
from pynput import mouse
from pynput.keyboard import Key, KeyCode, Listener as KeyboardListener, Controller as KeyboardController
from pynput.mouse import Button, Controller as MouseController, Listener as MouseListener
from inputUtils.FileTransferEventHandler import FileTransferEventHandler

# Network libraries
from network.IOManager import QueueManager

# Other libraries
from client.ClientState import ClientState, HiddleState
from config.ServerConfig import Clients

# Logging
from utils.Logging import Logger
from utils.netData import *


class ServerMouseListener:
    IGNORE_NEXT_MOVE_EVENT = 0.01
    MAX_DXDY_THRESHOLD = 150
    SCREEN_CHANGE_DELAY = 0.001
    EMULATION_STOP_DELAY = 0.5

    """
    :param send_function: Function to send data to the clients
    :param change_screen_function: Function to change the active screen
    :param get_active_screen: Function to get the active screen
    :param get_clients: Function to get the clients of the current screen
    :param screen_width: Width of the screen
    :param screen_height: Height of the screen
    :param screen_threshold: Threshold to change the screen
    """

    def __init__(self, change_screen_function: Callable, get_active_screen: Callable,
                 get_status: Callable,
                 screen_width: int, screen_height: int, screen_threshold: int = 5,
                 clients: Clients = None):

        self.send = QueueManager(None).send_mouse
        self.active_screen = get_active_screen
        self.change_screen = change_screen_function
        self.get_trasmission_status = get_status
        self.clients = clients
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.screen_treshold = screen_threshold

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

        # Aggiorna l'ultima posizione e tempo conosciuti
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
        clients = self.clients.get_connection(screen)
        if screen and clients:
            self.send(screen, format_command(f"mouse scroll {dx} {dy}"))
        return True


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

        self.file_transfer_handler = FileTransferEventHandler()
        self.command_pressed = False

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

    @staticmethod
    def get_current_clicked_directory():
        # Ottieni l'handle della finestra attiva
        hwnd = win32gui.GetForegroundWindow()

        # Ottieni il processo associato
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        process = psutil.Process(pid)

        # Verifica se il processo è Esplora Risorse
        if "explorer.exe" in process.name().lower():
            # Verifica se la finestra attiva è il Desktop
            # Desktop ha tipicamente titolo vuoto o nullo
            window_text = win32gui.GetWindowText(hwnd).strip()
            if window_text == 'Program Manager':
                # Restituisce il percorso del Desktop
                desktop_path = os.path.join(os.environ["USERPROFILE"], "Desktop")
                return desktop_path

            shell = client.CreateObject("Shell.Application")
            windows = shell.Windows()

            for window in windows:
                # Confronta l'handle della finestra attiva
                if int(hwnd) == int(window.HWND):
                    # Ottieni il percorso della directory attiva
                    directory = window.LocationURL
                    if directory.startswith("file:///"):
                        # Convert to a readable path
                        directory = urllib.parse.unquote(directory[8:].replace("/", "\\"))
                    return directory

        # Se il processo non è Esplora Risorse
        return None

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

        if key in [Key.cmd, Key.cmd_l]:
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

        if screen and clients:
            self.send(screen, format_command(f"keyboard release {data}"))


class ServerClipboardListener:
    def __init__(self, get_clients: Callable, get_active_screen: Callable):

        self.send = QueueManager(None).send_clipboard
        self.active_clients = get_clients
        self.active_screen = get_active_screen
        self._thread = None

        self.logger = Logger.get_instance().log

        self.last_clipboard_content = pyperclip.paste()  # Inizializza con il contenuto attuale della clipboard
        self.last_clipboard_files = None
        self.file_transfer_handler = FileTransferEventHandler()

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

    @staticmethod
    def get_clipboard_files():
        win32clipboard.OpenClipboard()
        try:
            # Controlla se il contenuto è di tipo CF_H ROP (file)
            if win32clipboard.IsClipboardFormatAvailable(win32con.CF_HDROP):
                # Ottieni i percorsi dei file copiati
                file_paths = win32clipboard.GetClipboardData(win32con.CF_HDROP)
                # Filter out directories and return only the last copied file
                files = [path for path in file_paths if os.path.isfile(path)]
                return files
            else:
                return None
        finally:
            # Chiudi la clipboard
            win32clipboard.CloseClipboard()

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
                    # Takes the last file in the list
                    file_info = self.get_file_info(current_clipboard_files[-1])
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
                self.logger(f"[CLIPBOARD] {e}", Logger.ERROR)


class ClientClipboardListener:
    def __init__(self):

        self.send = QueueManager(None).send_clipboard
        self._thread = None
        self.logger = Logger.get_instance().log

        self.last_clipboard_content = pyperclip.paste()  # Inizializza con il contenuto attuale della clipboard
        self.last_clipboard_files = None
        self.file_transfer_handler = FileTransferEventHandler()

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

    @staticmethod
    def get_clipboard_files():
        win32clipboard.OpenClipboard()
        try:
            # Controlla se il contenuto è di tipo CF_H ROP (file)
            if win32clipboard.IsClipboardFormatAvailable(win32con.CF_HDROP):
                # Ottieni i percorsi dei file copiati
                file_paths = win32clipboard.GetClipboardData(win32con.CF_HDROP)
                # Filter out directories and return only the last copied file
                files = [path for path in file_paths if os.path.isfile(path)]
                return files
            else:
                return None
        finally:
            # Chiudi la clipboard
            win32clipboard.CloseClipboard()

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
                    # Takes the last file in the list
                    file_info = self.get_file_info(current_clipboard_files[-1])
                    if file_info:
                        self.file_transfer_handler.handle_file_copy(file_info,
                                                                    self.file_transfer_handler.LOCAL_OWNERSHIP)
                        self.last_clipboard_files = current_clipboard_files

                elif current_clipboard_content != self.last_clipboard_content:
                    # Invia il contenuto della clipboard a tutti i client
                    self.send("all", format_command("clipboard ") + current_clipboard_content)
                    self.last_clipboard_content = current_clipboard_content

                time.sleep(0.5)
            except Exception as e:
                self.logger(f"[CLIPBOARD] {e}", Logger.ERROR)


class ClientMouseListener:
    def __init__(self, screen_width, screen_height, threshold):
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.threshold = threshold
        self.send = QueueManager(None).send_mouse
        self.client_status = ClientState()
        self.log = Logger.get_instance().log
        self._listener = MouseListener(on_move=self.handle_mouse, on_scroll=self.on_scroll, on_click=self.on_click)

    def get_listener(self):
        return self._listener

    def start(self):
        self._listener.start()

    def stop(self):
        self._listener.stop()

    def on_scroll(self, x, y, dx, dy):
        return True

    def on_click(self, x, y, button, pressed):
        return True

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
        return self.key_filter.get(key_data, key_data)

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
        key = self.get_key(key_data)

        if key_action == "press":
            # Gestione del caps lock
            if key_data is Key.caps_lock:
                if self.caps_lock_state:
                    self.controller.release(key_data)
                else:
                    self.controller.press(key_data)
                self.caps_lock_state = not self.caps_lock_state
            else:
                # Gestione dei tasti speciali
                if self.is_special_key(key_data):  # Special key handler
                    if Key.alt_gr in self.pressed_keys:
                        self.controller.release(Key.alt_gr)
                        self.pressed_keys.discard(Key.alt_gr)
                    if Key.alt in self.pressed_keys:
                        self.controller.release(Key.alt)
                        self.pressed_keys.discard(Key.alt)

                self.controller.press(key)
                self.pressed_keys.add(key)
        elif key_action == "release":
            if key in self.pressed_keys:
                self.controller.release(key)
                self.pressed_keys.discard(key)


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
        if "server_screen_size" in self.client_info:
            if not self.server_screen_width or not self.server_screen_height:
                self.server_screen_width, self.server_screen_height = self.client_info["server_screen_size"]

    def smooth_position(self, start_x, start_y, end_x, end_y, steps=50, sleep_time=0.00001):
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

    def smooth_move(self, dx, dy):
        current_x, current_y = self.mouse.position
        # Calcola la differenza tra la posizione iniziale e finale
        # Aumento dx e dy di un fattore di scala dove al numeratore vi è lo schermo più grande
        # e al denominatore il più piccolo

        # Arrotonda all intero successivo
        dx = int(dx)
        dy = int(dy)

        self.smooth_position(current_x, current_y, current_x + dx, current_y + dy, steps=1)

    def process_mouse_command(self, x, y, mouse_action, is_pressed):
        self.get_server_screen_size()
        if mouse_action == "position":
            target_x = max(0, min(x * self.screen_width, self.screen_width))  # Ensure target_x is within screen bounds
            target_y = max(0,
                           min(y * self.screen_height, self.screen_height))  # Ensure target_y is within screen bounds

            self.smooth_position(self.mouse.position[0], self.mouse.position[1], target_x, target_y)
        elif mouse_action == "move":
            # Denormalize the x and y values
            scale_x = self.server_screen_width / self.screen_width
            scale_y = self.server_screen_height / self.screen_height
            dx = x * scale_x
            dy = y * scale_y
            self.smooth_move(dx, dy)
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

    def smooth_scroll(self, x, y, delay=0.01, steps=2):
        """Smoothly scroll the mouse."""
        dx, dy = x, y
        for _ in range(steps):
            self.mouse.scroll(dx, dy)
            time.sleep(delay)


class ClientKeyboardListener:
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
        pythoncom.CoInitialize()
        try:
            # Ottieni l'handle della finestra attiva
            hwnd = win32gui.GetForegroundWindow()

            # Ottieni il processo associato
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            process = psutil.Process(pid)

            # Verifica se il processo è Esplora Risorse
            if "explorer.exe" in process.name().lower():
                # Verifica se la finestra attiva è il Desktop
                # Desktop ha tipicamente titolo vuoto o nullo
                window_text = win32gui.GetWindowText(hwnd).strip()
                if window_text == 'Program Manager' or window_text == '':
                    # Restituisce il percorso del Desktop
                    desktop_path = os.path.join(os.environ["USERPROFILE"], "Desktop")
                    # Clean the path
                    return desktop_path

                shell = client.CreateObject("Shell.Application")
                windows = shell.Windows()

                for window in windows:
                    # Compare the handle of the active window
                    if int(hwnd) == int(window.HWND):
                        # Get the path of the active directory
                        directory = window.LocationURL
                        if directory.startswith("file:///"):
                            # Convert URL format to Windows path
                            directory = urllib.parse.unquote(directory[8:].replace("/", "\\"))
                        return directory

            # Se il processo non è Esplora Risorse
            return None
        finally:
            # Libera le risorse COM
            pythoncom.CoUninitialize()

    def on_press(self, key: Key | KeyCode | None):

        if isinstance(key, Key):
            data = key.name
        else:
            data = key.char

        # Check if command + v is pressed
        if key in [Key.ctrl_l, Key.ctrl]:
            self.command_pressed = True
        elif data == "\x16" and self.command_pressed:
            current_dir = self.get_current_clicked_directory()
            if current_dir:
                self.file_transfer_handler.handle_file_paste(current_dir, self.file_transfer_handler.CLIENT_REQUEST)

    def on_release(self, key: Key | KeyCode | None):

        # Check if command is released
        if key in [Key.ctrl_l, Key.ctrl]:
            self.command_pressed = False
