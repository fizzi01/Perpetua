import socket
import threading
import time
from collections.abc import Callable

from inputUtils import InputHandler as inputHandler
from network.ClientSocket import ClientSocket, ConnectionHandlerFactory
from network.IOManager import ClientMessageQueueManager, QueueManager
from utils.Logging import Logger
from .ServerHandler import ServerCommandProcessor
from utils import screen_size


class Client:
    def __init__(self, server: str, port: int, threshold: int = 10,
                 wait: int = 5,
                 logging: bool = False, stdout: Callable = print, root=None, certfile=None, use_ssl=False,
                 keyfile=None):

        self._thread_pool = []
        self._started = False  # Main variable for client status, if False the client is stopped automatically
        self.lock = threading.RLock()

        # Logging and IO Managers are shared resources (should be initialized first)
        # Initialize logging
        self._initialize_logging(logging, stdout)
        # Initialize IO Managers
        self._initialize_io_managers()

        # Initialize the client
        self._initialize_client(server, port, threshold, wait, use_ssl, certfile, keyfile)

        # Screen transition handler
        self._is_transition = False
        self._initialize_screen_transition(root, threshold)

        # Initialize main thread
        self._initialize_main_thread()

        # Initialize listeners
        self._initialize_listeners()

        # Initalize input controllers
        self._initialize_input_controllers()

        self.lock = threading.Lock()
        self._running = False
        self._connected = None

        self.window = None  # TODO: Window(root) screen transition
        self.stdout = stdout

        self.on_screen = False  # TODO
        self.changed = False  # TODO
        self.transition_handler = None  # Thread che controlla on_screen, se False chiama self.window.show()
        # Il server invierà un comando per avvertire il client che non è più on_screen

    def _initialize_logging(self, logging, stdout):
        self.logging = logging
        self.stdout = stdout
        self.logger = Logger(self.logging, self.stdout)  # Initialize logger
        self.log = self.logger.log

    def _initialize_io_managers(self):
        self.messagesManager = ClientMessageQueueManager(self.client_socket)
        self.listenersQueueManager = QueueManager(self.messagesManager, keyboard=False)
        # Add IO Managers to the thread pool, they could be considered as threads too
        self._thread_pool.append(self.messagesManager)
        self._thread_pool.append(self.listenersQueueManager)

    def _initialize_client(self, server, port, threshold, wait, use_ssl, certfile, keyfile):
        self.server = server
        self.port = port
        self.threshold = threshold
        self.wait = wait
        self.client_socket = ClientSocket(host=server,
                                          port=port,
                                          wait=wait,
                                          use_ssl=use_ssl,
                                          certfile=certfile,
                                          keyfile=keyfile)

    def _initialize_connection_handler(self):
        self.processor = ServerCommandProcessor(self)
        self.connection_handler = ConnectionHandlerFactory.create_handler(self.client_socket, command_processor=self.processor.process_command)

    def _initialize_screen_transition(self, root, threshold):
        pass

    def _initialize_main_thread(self):
        self._client_thread = threading.Thread(target=self._run, daemon=True)
        self._thread_pool.append(self._client_thread)
        self._is_main_running_event = threading.Event()

    def _initialize_listeners(self):
        self.mouse_listener = inputHandler.ClientMouseListener(screen_width=self.screen_width,
                                                               screen_height=self.screen_height,
                                                               client_socket=self.client_socket,
                                                               threshold=self.threshold)

        self.clipboard_listener = inputHandler.ClientClipboardListener()
        self._listeners = [self.mouse_listener, self.clipboard_listener]

    def _initialize_input_controllers(self):
        self.screen_width, self.screen_height = screen_size()
        self.keyboard_controller = inputHandler.ClientKeyboardController()
        self.mouse_controller = inputHandler.ClientMouseController(self.screen_width, self.screen_height)

    def start(self):
        try:
            if not self._running:
                self._running = True
                self._client_thread.start()

                self._is_main_running_event.wait(timeout=1)
                if not self._is_main_running_event.is_set():
                    return self.stop()
                self._is_main_running_event.clear()

                self._start_listeners()

                # Start message processing thread
                self.messagesManager.start()
                self.listenersQueueManager.start()
            else:
                raise Exception("Client already started.")

        except Exception as e:
            self.log(f"{e}", Logger.ERROR)
            return self.stop()

        return True

    def _start_listeners(self):
        try:
            for listener in self._listeners:
                listener.start()
        except Exception as e:
            self.log(f"Error starting mouse listener: {e}")
            self.stop()

    def _run(self):
        self._is_main_running_event.set()
        while self._running:
            try:
                if self._connected:
                    time.sleep(self.wait)
                    continue

                self._connected = self.connection_handler.handle_connection()
            except socket.timeout as e:
                if self._running:
                    self.log("Connection timeout.", Logger.ERROR)
                    continue
                else:
                    break
            except socket.error as e:
                self.log(f"{e}", 2)
                continue
            except Exception as e:
                self.log(f"Error connecting to the server: {e}", 2)
                break

            self._connected = self.connection_handler.check_server_connection()

        self._is_main_running_event.clear()
        self._is_main_running_event.set()
        self.log("Client listening stopped.", Logger.WARNING)

    def stop(self):
        if self._running and self._is_main_running_event.is_set():
            try:

                self.log("Stopping client...", Logger.WARNING)
                self._running = False

                self.client_socket.close()
                self.connection_handler.stop()

                for threads in self._thread_pool:
                    if threads.is_alive():
                        threads.join()

                for listener in self._listeners:
                    listener.stop()

                if self._check_main_thread():
                    self.log("Client stopped.", Logger.INFO)
                    return True
            except Exception as e:
                self.log(f"{e}", 2)
                return False
        else:
            return True

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
