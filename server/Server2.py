import socket
import threading
import time
from queue import Queue, Empty
from typing import Union
from pynput import mouse

# Core utilities
from inputUtils import InputHandler
from window import Window

# Configuration
from utils import screen_size
from utils.netData import *
from config.ServerConfig import Client, Clients

# Network
from network.ServerSocket import ServerSocket

# Server
from server.ScreenTransition import ScreenTransitionFactory
from server.ScreenReset import ScreenResetStrategyFactory
from server.Command import CommandFactory
from server.ScreenState import ScreenStateFactory
from server.ClientHandler import ClientHandler

# Logging
from utils.Logging import Logger


# Observer Pattern per la gestione degli eventi di input
class InputEventSubject:
    def __init__(self):
        self._observers = []

    def add_observer(self, observer):
        self._observers.append(observer)

    def remove_observer(self, observer):
        self._observers.remove(observer)

    def notify_observers(self, event):
        for observer in self._observers:
            observer.update(event)


# Factory Pattern per la creazione dei ClientHandler
class ClientHandlerFactory:
    @staticmethod
    def create_client_handler(conn, addr, server):
        return ClientHandler(conn, addr, server.process_client_command, server.on_disconnect)


class Server:
    def __init__(self, host: str = "0.0.0.0", port: int = 5001, clients=None,
                 wait: int = 5, logging: bool = False, screen_threshold: int = 10, root=None, stdout=print):

        self._thread_pool = []
        self._started = False  # Main variable for server status, if False the server is stopped automatically
        self.lock = threading.RLock()

        # Initialize logging
        self._initialize_logging(logging, stdout)

        # Initialize server variables
        self._initialize_clients(clients)
        self._initialize_server_socket(host, port, wait)

        # Screen transition variables
        self._is_transition = False
        self.active_screen = None
        self._initialize_screen_transition(root, screen_threshold)

        # Initialize main threads
        self._initialize_threads()

        # Listeners
        self.listeners = []
        self.mouse_listener = None
        self.keyboard_listener = None
        self.clipboard_listener = None
        self.current_mouse_position = None
        self._initialize_input_listeners()

        # Message queue
        self._initialize_message_queue()

    def _initialize_clients(self, clients):
        if clients is None:
            clients = Clients({"left": Client()})
        self.clients = clients

    def _initialize_server_socket(self, host, port, wait):
        self.server_socket = ServerSocket(host, port, wait)
        self.wait = wait
        self._client_handlers = []

    def _initialize_logging(self, logging, stdout):
        self.logging = logging
        self.stdout = stdout
        self.logger = Logger(self.logging, self.stdout)  # Initialize logger

    def _initialize_screen_transition(self, root, screen_threshold):
        self.window = self._create_window(root)

        # Screen transition variables
        self.changed = threading.Event()
        self.screen_width, self.screen_height = screen_size()
        self.screen_threshold = screen_threshold

        # Screen transition orchestrator
        self._checker = threading.Thread(target=self.check_screen_transition, daemon=True)
        self._thread_pool.append(self._checker)

    def _initialize_threads(self):
        self._main_thread = threading.Thread(target=self._accept_clients, daemon=True)
        self._thread_pool.append(self._main_thread)
        self._is_main_running_event = threading.Event()

    def _initialize_input_listeners(self):
        self.input_event_subject = InputEventSubject()
        self.mouse_controller = mouse.Controller()
        self.current_mouse_position = self.mouse_controller.position

    def _initialize_message_queue(self):
        self.message_queue = Queue()
        self._process_queue_thread = threading.Thread(target=self._process_queue, daemon=True)
        self._thread_pool.append(self._process_queue_thread)

    def is_running(self):
        return self._started

    def join(self):
        self._main_thread.join()

    def start(self):
        try:
            self.server_socket.bind_and_listen()
            self.log(f"Server starting on {self.server_socket.host}:{self.server_socket.port}", 1)

            self._started = True

            # Threads initialization
            # Avvio del task asincrono per accettare i client
            self._main_thread.start()

            self._is_main_running_event.wait(timeout=1)
            if not self._is_main_running_event.is_set():
                return self.stop()
            self._is_main_running_event.clear()

            self._start_listeners()  # Start listeners
            self._checker.start()  # Start screen transition checker

            # Start message processing thread
            self._process_queue_thread.start()

            self.log(f"Server started.", 1)

        except Exception as e:
            self._started = False
            self.log(f"Server not started: {e}", 2)
            return self.stop()

        return True

    def stop(self):
        if not self._started and not self._is_main_running_event.is_set():
            return True

        self.log(f"Server stopping...", 1)
        self._started = False

        self.server_socket.close()

        # --- Start cleanup ----
        self.window.close()

        # Close all client handlers
        for handler in self._client_handlers:
            try:
                handler.stop()
            except Exception as e:
                self.log(f"Errore nella chiusura del client handler: {e}", 2)
                continue

        try:
            # Trigger checker
            self.changed.set()

            # Wait for all threads to finish
            for thread in self._thread_pool:
                if thread.is_alive():
                    thread.join()

            # Close listeners
            for listener in self.listeners:
                listener.stop()

            # Main thread checking
            if self._check_main_thread():
                self.log(f"Server stopped.", 1)
                return True
        except Exception as e:
            self.log(f"{e}", 2)
            return False

    def _accept_clients(self):
        self._is_main_running_event.set()
        while self._started:
            for key in self.clients.get_possible_positions():
                try:
                    if not self.clients.get_connection(key):
                        conn, addr = self.server_socket.accept()
                        self.log(f"Client handshake from {addr[0]}", 1)
                        # TODO : Inserire logica di handshake per sincronizzare inofrmazioni di connessione
                        #  come le costanti per i messaggi
                    else:
                        time.sleep(2)
                        continue
                except socket.timeout:
                    if self._started:
                        self.log("Waiting for clients ...", 1)
                        continue
                    else:
                        break
                except Exception as e:
                    if self._started:
                        self.log(f"{e}", 2)
                        continue
                    else:
                        break

                # Adding corresponding client to the list
                for pos in self.clients.get_possible_positions():
                    if self.clients.get_address(pos) == addr[0]:
                        self.clients.set_connection(pos, conn)

                        client_handler = ClientHandlerFactory.create_client_handler(conn, addr, self)
                        client_handler.start()
                        self._client_handlers.append(client_handler)
                        break

                time.sleep(1)

            # Handle client disconnections
            for key, client in self.clients.get_connected_clients().items():
                try:
                    # Check if the connection is still active
                    client.get_connection().send(b'\x00')
                except (socket.error, ConnectionResetError):
                    self.log(f"Client {key} disconnected.", 1)
                    self.on_disconnect(client.get_connection())

        self._is_main_running_event.clear()
        self._is_main_running_event.set()  # Set the event to True to indicate the main thread is terminated
        self.log("Server listening stopped.", 1)

    def on_disconnect(self, conn):
        # Set client connection to None and change screen to Host (None)
        for key in self.clients.get_possible_positions():
            if self.clients.get_connection(key) == conn:
                self.log(f"Client disconnected.", 1)
                self.clients.remove_connection(key)
                self._change_screen()
                return

    def _get_clients(self, screen) -> Union[socket.socket, None]:
        return self.clients.get_connection(screen)

    # Command Pattern per l'elaborazione dei comandi dei client
    def process_client_command(self, command):
        command_handler = CommandFactory.create_command(command, self)
        if command_handler:
            command_handler.execute()
        else:
            self.log(f"Comando non riconosciuto: {command}", 2)

    # State Pattern per la gestione delle transizioni di schermo
    def _change_screen(self, screen=None):
        state = ScreenStateFactory.get_screen_state(screen, self)
        with self.lock:
            state.handle()

    # Strategy Pattern per la gestione del logging
    def log(self, message, priority: int = 0):
        self.logger.log(message, priority)

    def update_mouse_position(self, x, y):
        self.current_mouse_position = (x, y)

    # Template Method Pattern per la sequenza di avvio del server
    def _start_listeners(self):
        try:
            self.listeners.append(self._setup_clipboard_listener())

            self.listeners.append(self._setup_keyboard_listener())

            time.sleep(0.2)  # Wait for the keyboard listener to start -> Crash fix
            self.listeners.append(self._setup_mouse_listener())

            for listener in self.listeners:
                # Check if the listeners are started
                if not listener.is_alive():
                    raise Exception(f"{listener} non avviato.")
        except Exception as e:
            self.log(f"{e}", 2)
            self.stop()

        self.log("Listeners started.")

    def _setup_mouse_listener(self):
        # Metodo hook per impostare il listener del mouse
        self.mouse_listener = InputHandler.ServerMouseListener(send_function=self._send_to_clients,
                                                               change_screen_function=self._change_screen,
                                                               get_active_screen=self._get_active_screen,
                                                               get_status=self._get_status,
                                                               get_clients=self._get_clients,
                                                               screen_width=self.screen_width,
                                                               screen_height=self.screen_height,
                                                               screen_threshold=self.screen_threshold,
                                                               update_mouse_position=self.update_mouse_position)
        self.mouse_listener.start()
        self.input_event_subject.add_observer(self.mouse_listener)
        return self.mouse_listener

    def _setup_keyboard_listener(self):
        # Metodo hook per impostare il listener della tastiera
        self.keyboard_listener = InputHandler.ServerKeyboardListener(send_function=self._send_to_clients,
                                                                     get_active_screen=self._get_active_screen,
                                                                     get_clients=self._get_clients)
        self.keyboard_listener.start()
        self.input_event_subject.add_observer(self.keyboard_listener)
        return self.keyboard_listener

    def _setup_clipboard_listener(self):
        self.clipboard_listener = InputHandler.ServerClipboardListener(send_function=self._send_to_clients,
                                                                       get_clients=self._get_clients,
                                                                       get_active_screen=self._get_active_screen)
        self.clipboard_listener.start()
        return self.clipboard_listener

    @staticmethod
    def _create_window(root):
        win = Window(root)
        win.minimize()
        return win

    def _check_main_thread(self):
        """
        Check for main thread termination
        :return True if main thread is terminated
        :raise Exception if main thread is still running
        """
        self._is_main_running_event.wait(timeout=5)

        if self._is_main_running_event.is_set():
            return True
        else:
            raise Exception("Thread principale non terminata.")

    def _get_active_screen(self):
        return self.active_screen

    def _get_status(self):
        return self._is_transition

    def _send_to_clients(self, screen: str, data):
        if screen == "all":
            for key in self.clients.get_possible_positions():
                if key:
                    self.message_queue.put((key, data))
        else:
            self.message_queue.put((screen, data))

    def _process_queue(self):
        while self._started:
            try:
                screen, data = self.message_queue.get(timeout=0.5)

                # Preparing data to send
                data = format_data(data)

                try:
                    conn = self._get_clients(screen)
                    if not conn:
                        raise KeyError
                except KeyError:
                    self.log(f"Errore nell'invio dei dati al client {screen}: Client non trovato.", 2)
                    continue
                except Exception as e:
                    self.log(f"Errore nell'invio dei dati al client {screen}: {e}", 2)
                    continue

                try:
                    # Split the command into chunks if it's too long
                    if len(data) > CHUNK_SIZE - len(END_DELIMITER):
                        chunks = [data[i:i + CHUNK_SIZE] for i in range(0, len(data), CHUNK_SIZE)]
                        for i, chunk in enumerate(chunks):
                            if i == len(chunks) - 1 and len(chunk) > CHUNK_SIZE - len(
                                    END_DELIMITER):  # Check if this is the last chunk and its length is > CHUNK_SIZE - END_DELIMITER
                                conn.send(chunk.encode())
                                conn.send(END_DELIMITER.encode())
                            elif i == len(
                                    chunks) - 1:  # This is the last chunk and its length is less than CHUNK_SIZE - END_DELIMITER
                                chunk = chunk.encode() + END_DELIMITER.encode()
                                conn.send(chunk)
                            else:
                                chunk = chunk.encode() + CHUNK_DELIMITER.encode()
                                conn.send(chunk)
                    else:
                        data = data.encode() + END_DELIMITER.encode()
                        conn.send(data)
                except socket.error as e:
                    self.log(f"Errore nell'invio dei dati al client {screen}: {e}", 2)
                    self._change_screen()
            except Empty:
                continue
            except Exception as e:
                self.log(f"Errore durante l'elaborazione della coda dei messaggi: {e}", 2)

    def _screen_toggle(self, screen):
        """
         Funzione per la gestione del cambio di schermata.
         Disabilita o meno lo schermo dell'host al cambio di schermata.
         """
        if not screen:
            # Disable screen transition
            #self.window.minimize()
            self.window.close()
        else:
            # Enable screen transition
            self.window = Window()
            self.window.maximize()

    def check_screen_transition(self):
        while self._started:
            self.changed.wait()
            self.log(f"[CHECKER] Checking screen transition...", )
            if self.changed.is_set():
                self.log(f"[CHECKER] Changing screen to {self.active_screen}", 1)

                with self.lock:
                    self._screen_toggle(self.active_screen)

                    screen_transition_state = ScreenTransitionFactory.get_transition_state(self.active_screen, self)
                    screen_transition_state.handle_transition()

                    self.changed.clear()
                    self._is_transition = True
                    self.log(f"[CHECKER] Screen transition to {self.active_screen} completed.")
                    time.sleep(0.2)

    def reset_mouse(self, param, y: float):
        screen_reset_strategy = ScreenResetStrategyFactory.get_reset_strategy(param, self)
        screen_reset_strategy.reset(y)

    def force_mouse_position(self, x, y):
        desired_position = (x, y)
        attempt = 0
        max_attempts = 50

        # Condizione per ridurre la frequenza degli aggiornamenti della posizione
        update_interval = 0.01  # intervallo tra gli aggiornamenti
        last_update_time = time.time()

        while not self._is_mouse_position_reached(desired_position) and attempt < max_attempts:
            current_time = time.time()
            if current_time - last_update_time >= update_interval:
                self.mouse_controller.position = desired_position
                attempt += 1
                last_update_time = current_time

            self._wait_for_mouse_position_update(desired_position)

    def _is_mouse_position_reached(self, desired_position):
        return self.mouse_controller.position == desired_position

    @staticmethod
    def _wait_for_mouse_position_update(desired_position):
        time.sleep(0.001)
