import socket
import threading
import time
from socket import socket

from socket import timeout
from pynput import mouse

# Core utilities
import inputUtils as InputHandler
from network.IOManager import ServerMessageQueueManager, QueueManager
from window import Window

# Configuration
from utils import screen_size
from config.ServerConfig import Client, Clients

# Network
from network.ServerSocket import ServerSocket, ConnectionHandlerFactory

# Server
from server.ScreenTransition import ScreenTransitionFactory
from server.ScreenReset import ScreenResetStrategyFactory
from server.ScreenState import ScreenStateFactory
from server.ClientHandler import ClientCommandProcessor

# Logging
from utils.Logging import Logger


class Server:
    def __init__(self, host: str = "0.0.0.0", port: int = 5001, clients=None,
                 wait: int = 5, logging: bool = False, screen_threshold: int = 10, root=None, stdout=print,
                 use_ssl: bool = False, certfile: str = None, keyfile: str = None):

        self._thread_pool = []
        self._started = False  # Main variable for server status, if False the server is stopped automatically
        self.lock = threading.RLock()

        # Logging and IO Managers are shared resources (should be initialized first)
        # Initialize logging
        self._initialize_logging(logging, stdout)
        # Initialize IO Managers
        self._initialize_io_managers()

        # Initialize server variables
        self._initialize_clients(clients)
        self._initialize_server_socket(host, port, wait)
        self._initalize_connections_handler(use_ssl, certfile, keyfile)

        # Screen transition variables
        self._is_transition = False
        self.active_screen = None
        self._initialize_screen_transition(root, screen_threshold)

        # Initialize main threads
        self._initialize_threads()

        # Input Listeners
        self.listeners = []
        self.mouse_listener = None
        self.keyboard_listener = None
        self.clipboard_listener = None
        self.current_mouse_position = None

        # Input controllers
        self._initialize_input_controllers()

    def _initialize_clients(self, clients):
        if clients is None:
            clients = Clients({"left": Client()})
        self.clients = clients

    def _initalize_connections_handler(self, use_ssl: bool = False, certfile: str = None, keyfile: str = None):
        self.use_ssl = use_ssl
        self.certfile = certfile
        self.keyfile = keyfile

        self.command_processor = ClientCommandProcessor(self)
        self.connection_handler = ConnectionHandlerFactory.create_handler(ssl_enabled=use_ssl,
                                                                          certfile=certfile,
                                                                          keyfile=keyfile,
                                                                          command_processor=self.command_processor.process_client_command)

    def _initialize_io_managers(self):
        self.messagesManager = ServerMessageQueueManager(self.get_connected_clients, self.get_client,
                                                         self.change_screen)
        self.listenersQueueManager = QueueManager(self.messagesManager)
        # Add IO Managers to the thread pool, they could be considered as threads too
        self._thread_pool.append(self.messagesManager)
        self._thread_pool.append(self.listenersQueueManager)

    def _initialize_server_socket(self, host, port, wait):
        self.server_socket = ServerSocket(host, port, wait)
        self.wait = wait
        self._client_handlers = []

    def _initialize_logging(self, logging, stdout):
        self.logging = logging
        self.stdout = stdout
        self.logger = Logger(self.logging, self.stdout)  # Initialize logger

    def _initialize_screen_transition(self, root, screen_threshold):
        self.window = Window()
        if not self.window.wait(timeout=2):
            self.log("Window not started.", Logger.ERROR)

        self.window.minimize()

        # Screen transition variables
        self.changed = threading.Event()
        self.block_transition = threading.Event()
        self.transition_completed = threading.Event()

        self.screen_width, self.screen_height = screen_size()
        self.screen_threshold = screen_threshold

        # Screen transition orchestrator
        self._checker = threading.Thread(target=self.check_screen_transition, daemon=True)
        self._securer = threading.Thread(target=self.secure_transaction, daemon=True)

        self._thread_pool.append(self._checker)
        self._thread_pool.append(self._securer)

    def _initialize_threads(self):
        self._main_thread = threading.Thread(target=self._accept_clients, daemon=True)
        self._thread_pool.append(self._main_thread)
        self._is_main_running_event = threading.Event()

    def _initialize_input_controllers(self):
        self.mouse_controller = mouse.Controller()
        self.current_mouse_position = self.mouse_controller.position

    def is_running(self):
        return self._started

    def start(self):
        try:
            self.server_socket.bind_and_listen()
            self.log(f"Server starting on {self.server_socket.host}:{self.server_socket.port}", Logger.INFO)

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
            self._securer.start()  # Start screen transition securer

            # Start message processing thread
            self.messagesManager.start()
            self.listenersQueueManager.start()

            self.log(f"Server started.", Logger.INFO)

        except Exception as e:
            self.log(f"Server not started: {e}", Logger.ERROR)
            return self.stop()

        return True

    def stop(self):
        if not self._started and not self._is_main_running_event.is_set():
            return True

        self.log(f"Server stopping ...", Logger.WARNING)
        self._started = False

        self.server_socket.close()

        # --- Start cleanup ----
        if self.window:
            self.window.stop()

        # Close connections handler
        self.connection_handler.stop()

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
                self.log(f"Server stopped.", Logger.WARNING)
                return True
        except Exception as e:
            self.log(f"{e}", Logger.ERROR)
            return False

    def _accept_clients(self):
        self._is_main_running_event.set()
        while self._started:
            try:
                conn, addr = self.server_socket.accept()
                self.log(f"Client handshake from {addr[0]}", Logger.INFO)

                # Check if the client is already connected
                if self.connection_handler.is_client_connected(addr):
                    self.log(f"Client {addr[0]} already connected.", Logger.WARNING)
                    conn.close()
                    continue

                self.connection_handler.handle_connection(conn, addr, self.clients)

            except timeout:
                if self._started:
                    self.connection_handler.check_client_connections()
                    continue
                else:
                    break
            except Exception as e:
                if self._started:
                    self.log(f"{e}", Logger.ERROR)
                    continue
                else:
                    break

        self._is_main_running_event.clear()
        self._is_main_running_event.set()  # Set the event to True to indicate the main thread is terminated
        self.log("Server listening stopped.", Logger.WARNING)

    def on_disconnect(self, conn):
        # Set client connection to None and change screen to Host (None)
        for key in self.clients.get_possible_positions():
            if self.clients.get_connection(key) == conn:
                self.log(f"Client {key} disconnected.", Logger.WARNING)
                self.clients.remove_connection(key)
                self.change_screen()
                return

    def get_client(self, screen) -> socket:
        return self.clients.get_connection(screen)

    def get_connected_clients(self):
        return self.clients.get_connected_clients()

    # State Pattern per la gestione delle transizioni di schermo
    def change_screen(self, screen=None):
        state = ScreenStateFactory.get_screen_state(screen, self)
        with self.lock:
            state.handle()

    # Strategy Pattern per la gestione del logging
    def log(self, message, priority: int = 0):
        self.logger.log(message, priority)

    def update_mouse_position(self, x, y):
        if not self.block_transition.is_set():
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
                    raise Exception(f"{listener} not started.")
        except Exception as e:
            raise Exception(f"{e}")

        self.log("Listeners started.")

    def _setup_mouse_listener(self):
        # Metodo hook per impostare il listener del mouse
        self.mouse_listener = InputHandler.ServerMouseListener(change_screen_function=self.change_screen,
                                                               get_active_screen=self._get_active_screen,
                                                               get_status=self._get_status,
                                                               screen_width=self.screen_width,
                                                               screen_height=self.screen_height,
                                                               screen_threshold=self.screen_threshold,
                                                               clients=self.clients)
        self.mouse_listener.start()
        return self.mouse_listener

    def _setup_keyboard_listener(self):
        # Metodo hook per impostare il listener della tastiera
        self.keyboard_listener = InputHandler.ServerKeyboardListener(get_active_screen=self._get_active_screen,
                                                                     get_clients=self.get_client)
        self.keyboard_listener.start()
        return self.keyboard_listener

    def _setup_clipboard_listener(self):
        self.clipboard_listener = InputHandler.ServerClipboardListener(get_clients=self.get_client,
                                                                       get_active_screen=self._get_active_screen)
        self.clipboard_listener.start()
        return self.clipboard_listener

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
        return self._is_transition if not self.block_transition.is_set() else False

    def _screen_toggle(self, screen):
        """
         Funzione per la gestione del cambio di schermata.
         Disabilita o meno lo schermo dell'host al cambio di schermata.
         """
        if self.window:
            if not screen:
                # Disable screen transition
                self.window.minimize()
            else:
                # Enable screen transition
                self.window.maximize()

    def check_screen_transition(self):
        while self._started:
            # Trigger the transition
            self.changed.wait()
            self.log(f"[CHECKER] Checking screen transition...", )
            if self.changed.is_set():
                self.log(f"Changing screen to {self.active_screen}", Logger.INFO)

                self._screen_toggle(self.active_screen)

                self._is_transition = True
                self.transition_completed.set()
                self.log(f"[CHECKER] Screen transition to {self.active_screen} completed.")
                self.changed.clear()

    def secure_transaction(self):
        """
        Assure that the screen transition is completed before starting a new one
        :return:
        """
        while self._started:
            self.log(f"[SECURER] Waiting for screen transition to complete...", Logger.DEBUG)
            # Trigger the transition
            self.changed.wait()

            # Set an event to block other ScreenState transitions invoked by _change_screen
            self.block_transition.set()
            self.log(f"[SECURER] Blocking screen transition.", Logger.DEBUG)
            self.log(f"[SECURER] Waiting for transition to complete...", Logger.DEBUG)
            # Wait for the transition to complete (max 5 seconds)
            self.transition_completed.wait(timeout=5)
            self.log(f"[SECURER] Transition completed.", Logger.DEBUG)

            self.transition_completed.clear()
            self.block_transition.clear()
            self.changed.clear()
            self.log(f"[SECURER] Securer completed.", Logger.DEBUG)

    def reset_mouse(self, param, y: float):
        screen_reset_strategy = ScreenResetStrategyFactory.get_reset_strategy(param, self)
        screen_reset_strategy.reset(y)

    def force_mouse_position(self, x, y):
        desired_position = (x, y)
        attempt = 0
        max_attempts = 10

        # Condizione per ridurre la frequenza degli aggiornamenti della posizione
        update_interval = 0.001  # intervallo tra gli aggiornamenti
        last_update_time = time.time()

        while not self._is_mouse_position_reached(desired_position) and attempt < max_attempts:
            current_time = time.time()
            if current_time - last_update_time >= update_interval:
                self.mouse_controller.position = desired_position
                attempt += 1
                last_update_time = current_time

    def _is_mouse_position_reached(self, desired_position, margin=100):
        """
        Check if the mouse position is reached, with a margin of error
        """
        current_position = self.mouse_controller.position
        return (abs(current_position[0] - desired_position[0]) <= margin and
                abs(current_position[1] - desired_position[1]) <= margin)
