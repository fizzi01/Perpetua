import socket
import threading
import time
from queue import Queue, Empty
from typing import Union
from pynput import mouse
import asyncio
from .ClientHandler import ClientHandler
from inputUtils import InputHandler
from window import Window
from utils import screen_size
from utils.netData import *
from config.ServerConfig import Client, Clients


# Singleton Pattern per il socket del server
class ServerSocket:
    _instance = None

    def __new__(cls, host: str, port: int, wait: int):
        if cls._instance is None:
            cls._instance = super(ServerSocket, cls).__new__(cls)
            cls._instance._initialize_socket(host, port, wait)
        return cls._instance

    def _initialize_socket(self, host: str, port: int, wait: int):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.settimeout(wait)
        self.host = host
        self.port = port

    def bind_and_listen(self):
        self.socket.bind((self.host, self.port))
        self.socket.listen()

    def accept(self):
        return self.socket.accept()

    def close(self):
        self.socket.close()


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


class ScreenTransitionFactory:
    """
    Factory class for creating screen transition state objects based on the active screen.
    """

    @staticmethod
    def get_transition_state(screen, server):
        if screen == "left":
            return LeftScreenTransition(server)
        elif screen == "right":
            return RightScreenTransition(server)
        elif screen == "up":
            return UpScreenTransition(server)
        elif screen == "down":
            return DownScreenTransition(server)
        else:
            return NoScreenTransition(server)


class ScreenTransitionState:
    """
    Base class for screen transition states.
    """

    def __init__(self, server):
        self.server = server

    def handle_transition(self):
        raise NotImplementedError("Subclasses should implement this method.")


class LeftScreenTransition(ScreenTransitionState):
    def handle_transition(self):
        self.server.reset_mouse("right", self.server.current_mouse_position[1])


class RightScreenTransition(ScreenTransitionState):
    def handle_transition(self):
        self.server.reset_mouse("left", self.server.current_mouse_position[1])


class UpScreenTransition(ScreenTransitionState):
    def handle_transition(self):
        self.server.reset_mouse("down", self.server.current_mouse_position[0])


class DownScreenTransition(ScreenTransitionState):
    def handle_transition(self):
        self.server.reset_mouse("up", self.server.current_mouse_position[0])


class NoScreenTransition(ScreenTransitionState):
    def handle_transition(self):
        # No transition needed
        pass


class ScreenResetStrategyFactory:
    """
    Factory class for creating screen reset strategy objects based on the screen parameter.
    """

    @staticmethod
    def get_reset_strategy(param, server):
        if param == "left":
            return LeftScreenResetStrategy(server)
        elif param == "right":
            return RightScreenResetStrategy(server)
        elif param == "up":
            return UpScreenResetStrategy(server)
        elif param == "down":
            return DownScreenResetStrategy(server)
        else:
            raise ValueError("Invalid screen parameter.")


class ScreenResetStrategy:
    """
    Base class for screen reset strategies.
    """

    def __init__(self, server):
        self.server = server

    def reset(self, y: float):
        raise NotImplementedError("Subclasses should implement this method.")


class LeftScreenResetStrategy(ScreenResetStrategy):
    def reset(self, y: float):
        self.server.force_mouse_position(self.server.screen_threshold + 25, y)
        self.server.log(f"Moving mouse to x: {self.server.screen_threshold + 15}, y:{y}")


class RightScreenResetStrategy(ScreenResetStrategy):
    def reset(self, y: float):
        self.server.force_mouse_position(self.server.screen_width - self.server.screen_threshold - 25, y)
        self.server.log(f"Moving mouse to x: {self.server.screen_width - self.server.screen_threshold - 15}, y:{y}")


class UpScreenResetStrategy(ScreenResetStrategy):
    def reset(self, y: float):
        self.server.force_mouse_position(y, self.server.screen_threshold + 10)
        self.server.log(f"Moving mouse to x: {y}, y:{self.server.screen_threshold + 10}")


class DownScreenResetStrategy(ScreenResetStrategy):
    def reset(self, y: float):
        self.server.force_mouse_position(y, self.server.screen_height - self.server.screen_threshold - 10)
        self.server.log(f"Moving mouse to x: {y}, y:{self.server.screen_height - self.server.screen_threshold - 10}")


# Command Pattern per gestire i comandi dei client
class Command:
    def execute(self):
        raise NotImplementedError("Subclasses should implement this!")


class ClipboardCommand(Command):
    def __init__(self, server, data):
        self.server = server
        self.data = data

    def execute(self):
        text = extract_text(self.data)
        self.server.clipboard_listener.set_clipboard(text)


class ReturnCommand(Command):
    def __init__(self, server, direction):
        self.server = server
        self.direction = direction

    def execute(self):
        # Implementazione della logica di ritorno dello schermo
        if self.server.active_screen == "left" and self.direction == "right":
            with self.server.lock:
                self.server.active_screen = None
                self.server._is_transition = False
                self.server.changed.set()
                self.server.reset_mouse("left", self.server.current_mouse_position[1])
        elif self.server.active_screen == "right" and self.direction == "left":
            with self.server.lock:
                self.server.active_screen = None
                self.server._is_transition = False
                self.server.changed.set()
                self.server.reset_mouse("right", self.server.current_mouse_position[1])
        elif self.server.active_screen == "up" and self.direction == "down":
            with self.server.lock:
                self.server.active_screen = None
                self.server._is_transition = False
                self.server.changed.set()
                self.server.reset_mouse("up", self.server.current_mouse_position[0])
        elif self.server.active_screen == "down" and self.direction == "up":
            with self.server.lock:
                self.server.active_screen = None
                self.server._is_transition = False
                self.server.changed.set()
                self.server.reset_mouse("down", self.server.current_mouse_position[0])


class CommandFactory:
    @staticmethod
    def create_command(command, server):
        parts = extract_command_parts(command)
        if parts[0] == 'clipboard':
            return ClipboardCommand(server, parts[1])
        elif parts[0] == 'return':
            return ReturnCommand(server, parts[1])
        return None


# Factory Pattern per la creazione dei ClientHandler
class ClientHandlerFactory:
    @staticmethod
    def create_client_handler(conn, addr, server):
        return ClientHandler(conn, addr, server.process_client_command, server.on_disconnect, logger=server.log)


# State Pattern per la gestione delle transizioni di schermo
class ScreenState:
    def handle(self):
        raise NotImplementedError("Subclasses should implement this!")


class NoStateTransition(ScreenState):
    def __init__(self, server):
        self.server = server

    def handle(self):
        pass


class NoScreenState(ScreenState):
    def __init__(self, server):
        self.server = server

    def handle(self):
        print("NoScreenState")
        self.server.active_screen = None
        self.server._is_transition = False
        self.server.changed.set()


class LeftScreenState(ScreenState):
    def __init__(self, server):
        self.server = server

    def handle(self):
        self.server.active_screen = "left"
        self.server._is_transition = False
        self.server.changed.set()
        self.server.reset_mouse("left", self.server.current_mouse_position[1])


class UpScreenState(ScreenState):
    def __init__(self, server):
        self.server = server

    def handle(self):
        self.server.active_screen = "up"
        self.server._is_transition = False
        self.server.changed.set()
        self.server.reset_mouse("up", self.server.current_mouse_position[0])


class RightScreenState(ScreenState):
    def __init__(self, server):
        self.server = server

    def handle(self):
        self.server.active_screen = "right"
        self.server._is_transition = False
        self.server.changed.set()
        self.server.reset_mouse("right", self.server.current_mouse_position[1])


class DownScreenState(ScreenState):
    def __init__(self, server):
        self.server = server

    def handle(self):
        self.server.active_screen = "down"
        self.server._is_transition = False
        self.server.changed.set()
        self.server.reset_mouse("down", self.server.current_mouse_position[0])


class ScreenStateFactory:
    @staticmethod
    def get_screen_state(screen, server):

        # First check if the screen is the same as the active screen
        if screen == server.active_screen:
            return NoStateTransition(server)  # No transition needed

        # Check if the screen is None
        if not screen:
            return NoScreenState(server)

        # Check if the client is present
        if screen not in server.clients.get_possible_positions():
            return NoStateTransition(server)  # No transition needed

        # Check if client is connected
        if not server.clients.get_connection(screen):
            return NoStateTransition(server)  # No transition needed

        # Fall back to None only if both screens are not None
        if screen and server.active_screen:
            return NoScreenState(server)  # Transition to None

        # Check if the screen is valid
        if screen == "left":
            return LeftScreenState(server)
        elif screen == "right":
            return RightScreenState(server)
        elif screen == "up":
            return UpScreenState(server)
        elif screen == "down":
            return DownScreenState(server)
        else:
            return NoScreenState(server)


# Strategy Pattern per la gestione del logging
class LoggingStrategy:
    def log(self, stdout, message, priority):
        raise NotImplementedError("Subclasses should implement this!")


class ConsoleLoggingStrategy(LoggingStrategy):
    def log(self, stdout, message, priority):
        if priority == 2:
            stdout(f"\033[91mERROR: {message}\033[0m")
        elif priority == 1:
            stdout(f"\033[94mINFO: {message}\033[0m")
        elif priority == 0:
            stdout(f"\033[92mDEBUG: {message}\033[0m")
        else:
            stdout(message)


class SilentLoggingStrategy(LoggingStrategy):
    def log(self, stdout, message, priority):
        if priority == 2:
            stdout(f"\033[91mERROR: {message}\033[0m")
        elif priority == 1:
            stdout(f"\033[94mINFO: {message}\033[0m")
        else:
            pass


class LoggingStrategyFactory:
    @staticmethod
    def get_logging_strategy(logging_enabled):
        if logging_enabled:
            return ConsoleLoggingStrategy()
        else:
            return SilentLoggingStrategy()


class Server:
    def __init__(self, host: str = "0.0.0.0", port: int = 5001, clients=None,
                 wait: int = 5, logging: bool = False, screen_threshold: int = 10, root=None, stdout=print):

        # Inizializzazione dei client utilizzando la nuova classe Clients
        if clients is None:
            clients = Clients({"left": Client()})
        self.clients = clients

        self.logging = logging
        self.lock = threading.RLock()
        self.stdout = stdout

        # Singleton per il socket del server
        self.server_socket = ServerSocket(host, port, wait)
        self.wait = wait

        # List of client handlers started
        self._client_handlers = []

        # Main variable for server status, if False the server is stopped automatically
        self._started = False

        # Window initialization for screen transition
        self.window = self._create_window(root)

        # Screen transition variables
        self.active_screen = None
        self.changed = threading.Event()
        self.screen_width, self.screen_height = screen_size()
        self.screen_threshold = screen_threshold

        # Screen transition orchestrator
        self._checker = threading.Thread(target=self.check_screen_transition, daemon=True)
        self._is_transition = False

        # Server core thread
        self._main_thread = self._main_thread = threading.Thread(target=self._accept_clients, daemon=True)
        self._is_main_running_event = threading.Event()

        # Input listeners as observers
        self.input_event_subject = InputEventSubject()
        self.mouse_listener = None  # Input listener for mouse
        self.keyboard_listener = None  # Input listener for keyboard
        self.mouse_controller = mouse.Controller()  # Mouse controller for mouse position
        self.current_mouse_position = self.mouse_controller.position
        self.clipboard_listener = None  # Clipboard listener

        # Queue for client messages
        self.message_queue = Queue()
        self._process_queue_thread = threading.Thread(target=self._process_queue, daemon=True)

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
            # Wait for checker to finish
            if self._checker.is_alive():
                self._checker.join()  # Screen transition orchestrator thread

            if self.mouse_listener:
                self.mouse_listener.stop()
            if self.keyboard_listener:
                self.keyboard_listener.stop()

            if self._process_queue_thread.is_alive():
                self._process_queue_thread.join()

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
        logging_strategy = LoggingStrategyFactory.get_logging_strategy(self.logging)
        logging_strategy.log(self.stdout, message, priority)

    def update_mouse_position(self, x, y):
        self.current_mouse_position = (x, y)

    # Template Method Pattern per la sequenza di avvio del server
    def _start_listeners(self):
        try:
            self._setup_clipboard_listener()

            self._setup_keyboard_listener()
            time.sleep(0.2)
            self._setup_mouse_listener()

            if not self.mouse_listener.is_alive():
                raise Exception("Mouse listener not started")
            if not self.keyboard_listener.is_alive():
                raise Exception("Keyboard listener not started")
        except Exception as e:
            self.log(f"Errore nell'avvio dei listener: {e}", 2)
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
                                                               update_mouse_position=self.update_mouse_position,
                                                               logger=self.log)
        self.mouse_listener.start()
        self.input_event_subject.add_observer(self.mouse_listener)

    def _setup_keyboard_listener(self):
        # Metodo hook per impostare il listener della tastiera
        self.keyboard_listener = InputHandler.ServerKeyboardListener(send_function=self._send_to_clients,
                                                                     get_active_screen=self._get_active_screen,
                                                                     get_clients=self._get_clients,
                                                                     logger=self.log)
        self.keyboard_listener.start()
        self.input_event_subject.add_observer(self.keyboard_listener)

    def _setup_clipboard_listener(self):
        self.clipboard_listener = InputHandler.ServerClipboardListener(send_function=self._send_to_clients,
                                                                       get_clients=self._get_clients,
                                                                       get_active_screen=self._get_active_screen)
        self.clipboard_listener.start()

    # Factory Method Pattern per la gestione della finestra
    @staticmethod
    def _create_window(root):
        return Window(root)

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
            self.log(f"Sending data to {screen}: {data}", 0)
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
