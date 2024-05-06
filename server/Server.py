import socket
import threading
import time
from time import sleep

from pynput import mouse

from .ClientHandler import ClientHandler
from inputUtils import InputHandler
from window import Window
from utils import screen_size

class Server:
    """
    Classe per la gestione del server.

    :param host: Indirizzo del server
    :param port: Porta del server
    :param clients: Dizionario contenente le posizioni dei client abilitate
    :param screen_width: Larghezza dello schermo
    :param screen_height: Altezza dello schermo
    :param wait: Tempo di attesa per la connessione dei client
    :param logging: Enable logs
    :param screen_threshold: Soglia per la transizione dello schermo
    :param root: Main gui window
    :param stdout: Funzione per la stampa dei messaggi
    """

    def __init__(self, host: str = "0.0.0.0", port: int = 5001, clients=None,
                 wait: int = 5, logging: bool = False, screen_threshold: int = 10, root=None, stdout=print):

        if clients is None:
            clients = {"left": {"conn": None, "addr": None}}

        self.logging = logging
        self.host = host
        self.port = port
        self.clients = clients  # TODO: Comfiguration

        self.lock = threading.Lock()

        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.settimeout(wait)
        self.wait = wait

        # List of client handlers started
        self._client_handlers = []

        # Main variable for server status, if False the server is stopped automatically
        self._started = False

        # Window initialization for screen transition
        self.window = Window(root)
        self.stdout = stdout

        # Screen transition variables
        self.active_screen = None
        self._changed = False
        self.screen_width, self.screen_height = screen_size()

        self.screen_threshold = screen_threshold

        # Screen transition orchestrator
        self._checker = threading.Thread(target=self.check_screen_transition)

        # Server core thread
        self._main_thread = threading.Thread(target=self._accept_clients)
        self._is_main_running = False

        # Input listeners
        self.mouse_listener = None  # Input listener for mouse
        self.keyboard_listener = None  # Input listener for keyboard
        self.mouse_controller = mouse.Controller()  # Mouse controller for mouse position

    def start(self):
        try:
            self.server_socket.bind((self.host, self.port))
            self.server_socket.listen()
            self.log(f"Server starting on {self.host}:{self.port}", 1)

            self._started = True

            # Threads initialization
            self._main_thread.start()  # Accept clients
            self._is_main_running = True

            self._start_listeners()  # Start listeners
            self._checker.start()  # Start screen transition checker

            self.log(f"Server started.", 1)

        except Exception as e:
            self._started = False
            self.log(f"Errore nell'avvio del server: {e}")
            return self.stop()

        return True

    def stop(self):

        if not self._started and not self._is_main_running:
            return True

        self.log(f"Server stopping...", 1)
        # Stops threads
        self._started = False

        self.server_socket.close()

        # --- Start cleanup ----
        # Close window for transition
        self.window.close()

        # Close all client handlers
        for handler in self._client_handlers:
            try:
                handler.stop()
            except Exception as e:
                self.log(f"Errore nella chiusura del client handler: {e}", 2)
                continue

        try:
            self._checker.join()  # Screen transition orchestrator thread
            self.mouse_listener.stop()  # Mouse listener
            self.keyboard_listener.stop()  # Keyboard listener
            self.server_socket.close()  # Server socket

            # Main thread checking
            if self._check_main_thread():
                self.log(f"Server stopped.", 1)
                return True
        except Exception as e:
            self.log(f"{e}", 2)
            return False

    """
    Check for main thread termination
    :return True if main thread is terminated
    :raise Exception if main thread is still running
    """

    def _check_main_thread(self):
        max_retry = 5
        retry = 0
        while self._is_main_running and retry < max_retry:
            sleep(0.5)
            retry += 1

        if retry < max_retry:
            return True
        else:
            if self._is_main_running:
                raise Exception("Thread principale non terminata.")

    def _start_listeners(self):
        self.mouse_listener = InputHandler.ServerMouseListener(send_function=self._send_to_clients,
                                                               change_screen_function=self._change_screen,
                                                               get_active_screen=self._get_active_screen,
                                                               get_clients=self._get_clients,
                                                               screen_width=self.screen_width,
                                                               screen_height=self.screen_height,
                                                               screen_threshold=self.screen_threshold)

        self.keyboard_listener = InputHandler.ServerKeyboardListener(send_function=self._send_to_clients,
                                                                     get_active_screen=self._get_active_screen,
                                                                     get_clients=self._get_clients)
        try:
            self.mouse_listener.start()
            time.sleep(2)
            self.keyboard_listener.start()
        except Exception as e:
            self.log(f"Errore nell'avvio del mouse listener: {e}", 2)

        self.log("Mouse listener started.")

    def _accept_clients(self):
        while self._started:
            for key in self.clients:
                try:
                    if not self.clients[key]['conn']:
                        conn, addr = self.server_socket.accept()
                        self.log(f"Client handshake from {addr[0]}", 1)
                    else:
                        sleep(float(self.wait))
                        continue
                except socket.timeout:
                    if self._started:  # Check if server is still running
                        self.log("Waiting for clients.")
                        continue
                    else:
                        break
                except Exception as e:
                    if self._started:  # Check if server is still running
                        self.log(f"{e}", 2)
                        continue
                    else:
                        break

                # Adding corresponding client to the list
                for pos, info in self.clients.items():
                    if info['addr'] == addr[0]:
                        self.clients[pos]['conn'] = conn

                        client_handler = ClientHandler(conn, addr, self._process_client_command, self._on_disconnect,
                                                       logger=self.log)
                        client_handler.start()
                        self._client_handlers.append(client_handler)
                        break

        self._is_main_running = False
        self.log("Server listening stopped.", 1)

    def _on_disconnect(self, conn):
        # Set client connection to None and change screen to Host (None)
        for key, info in self.clients.items():
            if info['conn'] == conn:
                info['conn'] = None
                self._change_screen(None)
                return

    def _get_active_screen(self):
        return self.active_screen

    def _get_clients(self, screen):
        if screen:
            try:
                return self.clients[screen]['conn']
            except KeyError:
                return None
        else:
            return None

    def _process_client_command(self, command):
        parts = command.split()
        try:
            y = float(parts[2]) * self.screen_height    # Denormalize y
        except Exception:
            y = self.current_mouse_position[1]

        if parts[0] == 'return':
            if self.active_screen == "left" and parts[1] == "right":
                with self.lock:
                    self.active_screen = None
                    self._changed = True
                    self._reset_mouse("left", y)
            elif self.active_screen == "right" and parts[1] == "left":
                with self.lock:
                    self.active_screen = None
                    self._changed = True
                    self._reset_mouse("right", y)
            elif self.active_screen == "up" and parts[1] == "down":
                with self.lock:
                    self.active_screen = None
                    self._changed = True
                    self._reset_mouse("up", y)
            elif self.active_screen == "down" and parts[1] == "up":
                with self.lock:
                    self.active_screen = None
                    self._changed = True
                    self._reset_mouse("down", y)

    def _send_to_clients(self, screen, data):

        try:
            conn = self.clients[screen].get('conn')
        except KeyError:
            self.log(f"Errore nell'invio dei dati al client {screen}: Client non trovato.", 2)
            return
        except Exception as e:
            self.log(f"Errore nell'invio dei dati al client {screen}: {e}", 2)
            return

        if conn:
            try:
                conn.send(data.encode())
            except socket.error as e:
                self.log(f"Errore nell'invio dei dati al client {screen}: {e}", 2)
                self._change_screen(None)

    def _change_screen(self, screen):

        # Check if screen is already active
        if self.active_screen == screen:
            return

        # Check if is screen connected, if not return and set active_screen to None
        if screen and self._get_clients(screen) is None:
            if self.active_screen:
                with self.lock:
                    self.active_screen = None
                    self._changed = True
            return

        # Change screen and set mouse position
        with self.lock:
            self.active_screen = screen
            self.current_mouse_position = self.mouse_controller.position
            self._changed = True

    """
    Funzione per la gestione del cambio di schermata.
    Disabilita o meno lo schermo dell'host al cambio di schermata.
    """

    def _screen_toggle(self, screen):
        if not screen:
            # Disable screen transition
            self.window.minimize()
        else:
            # Enable screen transition
            self.window.maximize()

    def check_screen_transition(self):
        self.log(f"Transition Checker started.")
        while self._started:
            sleep(0.1)
            if self._changed:
                self.log(f"Changing screen to {self.active_screen}", 1)

                if self.active_screen == "left":
                    self._reset_mouse("right", self.current_mouse_position[1])
                elif self.active_screen == "right":
                    self._reset_mouse("left", self.current_mouse_position[1])
                elif self.active_screen == "up":
                    self._reset_mouse("down", self.current_mouse_position[0])
                elif self.active_screen == "down":
                    self._reset_mouse("up", self.current_mouse_position[0])

                self._screen_toggle(self.active_screen)

                with self.lock:
                    self._changed = False

    def _reset_mouse(self, param, y:float):
        if param == "left":
            self.mouse_controller.position = (self.screen_threshold + 50, y)
            self.log(f"Moving mouse to x: {self.screen_threshold + 100}, y:{y}")
        elif param == "right":
            self.mouse_controller.position = (self.screen_width - self.screen_threshold - 50, y)
            self.log(f"Moving mouse to x: {self.screen_width - self.screen_threshold - 50}, y:{y}")
        elif param == "up":
            self.mouse_controller.position = (y, self.screen_threshold + 50)
            self.log(f"Moving mouse to x: {y}, y:{self.screen_threshold + 5}")
        elif param == "down":
            self.mouse_controller.position = (y, self.screen_height - self.screen_threshold - 50)
            self.log(f"Moving mouse to x: {y}, y:{self.screen_height - self.screen_threshold - 50}")

    def log(self, message, priority: int = 0):
        if priority == 2:
            print(f"ERROR: {message}")
            self.stdout(f"ERROR: {message}")
        elif priority == 1:
            print(f"INFO: {message}")
            self.stdout(f"INFO: {message}")
        elif self.logging:
            print(message)
            self.stdout(message)
