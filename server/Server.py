import socket
import threading
from time import sleep

from pynput import mouse

from .ClientHandler import ClientHandler
from inputUtils import InputHandler
from window import Window


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
    """

    def __init__(self, host: str = "0.0.0.0", port: int = 5001, clients=None, screen_width=1920, screen_height=1080,
                 wait: int = 5, logging: bool = False):

        if clients is None:
            clients = {"left": {"conn": None, "addr": None}}

        self.logging = logging
        self.host = host
        self.port = port
        self.clients = clients
        self.lock = threading.Lock()

        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.settimeout(wait)
        self.wait = wait

        self._client_handlers = []
        self._started = False

        # Window initialization for screen transition
        self.window = Window()

        self.active_screen = None
        self._changed = False
        self.screen_width = screen_width
        self.screen_height = screen_height
        self._checker = threading.Thread(target=self.check_screen_transition)

        self._main_thread = threading.Thread(target=self._accept_clients)

        self.mouse_listener = None
        self.keyboard_listener = None
        self.mouse_controller = mouse.Controller()

    def start(self):
        try:
            self.server_socket.bind((self.host, self.port))
            self.server_socket.listen()
            self.log(f"Server starting on {self.host}:{self.port}")

            # Threads initialization
            self._main_thread.start()  # Accept clients
            self._start_listeners()  # Start listeners
            self._checker.start()  # Start screen transition checker

            self._started = True
            self.log(f"Server started.")

            # Start the window that manages the screen transition
            self.window.run()

        except Exception as e:
            self.log(f"Errore nell'avvio del server: {e}")
            return self.stop()

        return True

    def stop(self):
        self.log(f"Server stopping.", 1)
        if not self._started:
            return False

        self.window.close()

        for handler in self._client_handlers:
            try:
                handler.join()
            except Exception as e:
                self.log(f"Errore nella chiusura del client handler: {e}")
                continue
        try:
            self._checker.join()
            self.mouse_listener.get_listener().stop()
            self.server_socket.close()
            return True
        except Exception as e:
            self.log(f"Errore nella chiusura del server: {e}", 1)
            return False

    def _start_listeners(self):
        self.mouse_listener = InputHandler.ServerMouseListener(self._send_to_clients, self._change_screen,
                                                               self._get_active_screen,
                                                               self._get_clients, self.screen_width, self.screen_height,
                                                               self.lock)
        try:
            self.mouse_listener.get_listener().start()
        except Exception as e:
            self.log(f"Errore nell'avvio del mouse listener: {e}", 1)
            raise Exception("Errore nell'avvio del mouse listener.")
        self.log("Mouse listener started.")

    def _accept_clients(self):
        while True:
            for key in self.clients:
                try:
                    if not self.clients[key]['conn']:
                        conn, addr = self.server_socket.accept()
                    else:
                        sleep(float(self.wait))
                        continue
                except socket.timeout:
                    self.log("Waiting for clients.")
                    continue

                self.clients[key]['conn'] = conn
                self.clients[key]['addr'] = addr
                client_handler = ClientHandler(conn, addr, self._process_client_command, self._on_disconnect)
                client_handler.start()
                self._client_handlers.append(client_handler)

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

        if parts[0] == 'return':
            if self.active_screen == "left" and parts[1] == "right":
                with self.lock:
                    self.active_screen = None
                    self._changed = True
                    self._reset_mouse("left", self.current_mouse_position[1])
            elif self.active_screen == "right" and parts[1] == "left":
                with self.lock:
                    self.active_screen = None
                    self._changed = True
                    self._reset_mouse("right", self.current_mouse_position[1])
            elif self.active_screen == "up" and parts[1] == "down":
                with self.lock:
                    self.active_screen = None
                    self._changed = True
                    self._reset_mouse("up", self.current_mouse_position[0])
            elif self.active_screen == "down" and parts[1] == "up":
                with self.lock:
                    self.active_screen = None
                    self._changed = True
                    self._reset_mouse("down", self.current_mouse_position[0])

    def _send_to_clients(self, screen, data):

        try:
            conn = self.clients[screen].get('conn')
        except KeyError:
            self.log(f"Errore nell'invio dei dati al client {screen}: Client non trovato.", 1)
            return
        except Exception as e:
            self.log(f"Errore nell'invio dei dati al client {screen}: {e}", 1)
            return

        if conn:
            try:
                conn.send(data.encode())
            except socket.error as e:
                self.log(f"Errore nell'invio dei dati al client {screen}: {e}", 1)
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
            self.log(f"Changing screen to {screen}")
            self._changed = True


    """
    Funzione per la gestione del cambio di schermata.
    Disabilita o meno lo schermo dell'host al cambio di schermata.
    """
    def _screen_toggle(self, screen):
        if not screen:
            self.window.minimize()
        else:
            self.window.maximize()

    def check_screen_transition(self):
        self.log(f"Transition Checker started.")
        while True:
            sleep(0.1)
            if self._changed:
                self.log("!!! Checking for screen transition !!!")

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

    def _reset_mouse(self, param, y):
        if param == "left":
            self.mouse_controller.position = (100, int(float(y)))
            self.log(f"Moving mouse to x: {100}, y:{y}")
        elif param == "right":
            self.mouse_controller.position = (self.screen_width - 100, int(float(y)))
            self.log(f"Moving mouse to x: {self.screen_width - 100}, y:{y}")
        elif param == "down":
            self.mouse_controller.position = (int(float(y)), 100)
            self.log(f"Moving mouse to x: {y}, y:{100}")
        elif param == "up":
            self.mouse_controller.position = (int(float(y)), self.screen_height - 100)
            self.log(f"Moving mouse to x: {y}, y:{self.screen_height - 100}")

    def log(self, message, priority: int = 0):
        if priority == 1:
            print(f"ERROR: {message}")
        elif self.logging:
            print(message)
