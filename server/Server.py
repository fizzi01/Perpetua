from threading import Lock
from typing import Optional

# General interfaces
from utils.Interfaces import (
    IServer,
    IBaseSocket,
    IServerSocket,
    IClients,
    IScreenTransitionController,
    IMouseController,
    IEventBus,
    IKeyboardController,
    IClipboardController,
)

# Contexts
from utils.Interfaces import (
    IServerContext,
    IFileTransferContext,
    IScreenContext,
    IControllerContext
)

# Services
from utils.Interfaces import (
    IMessageService,
    IServerConnectionService,
    IFileTransferService,
    IInputListenerService,
    IInputControllerService,
    IServerScreenMouseService
)

# Logging
from utils.Logging import Logger


class Server(IFileTransferContext, IControllerContext, IScreenContext, IServerContext, IServer):
    def __init__(self, server_socket: IServerSocket, clients: IClients = None, screen_threshold: int = 10,
                 logger: Logger = None, screen_width: int = 0, screen_height: int = 0):

        # Screen variables
        self.screen_width, self.screen_height = screen_width, screen_height
        self.screen_threshold = screen_threshold

        self._thread_pool = []
        self._started = False  # Main variable for server status, if False the server is stopped automatically

        # Locks for context and screen transition
        self._context_lock = Lock()

        # Initialize logging
        self.logger: Logger = logger

        # Initialize IO Managers
        self.message_service: IMessageService | None = None

        # Initialize server variables
        self.clients: IClients | None = clients
        self.server_socket: IServerSocket = server_socket

        # Screen transition variables
        self.screen_transition_controller: IScreenTransitionController | None = None
        self.active_screen: str | None = None

        # Connection service
        self.connection_service: IServerConnectionService | None = None

        # Event Bus
        self.event_bus: IEventBus | None = None

        # Input Services
        self.listeners_service: IInputListenerService | None = None
        self.controller_service: IInputControllerService | None = None
        self.current_mouse_position: tuple | None = None
        self.mouse_service: IServerScreenMouseService | None = None

        # File Transfer Service
        self._file_transfer_service: IFileTransferService | None = None

    """ --- Server --- """
    def set_event_bus(self, event_bus: 'IEventBus') -> None:
        self.event_bus = event_bus
        self._thread_pool.append(self.event_bus)

    def set_connection_service(self, connection_service: 'IServerConnectionService') -> None:
        self.connection_service = connection_service
        self._thread_pool.append(self.connection_service)

    def set_message_service(self, service: 'IMessageService') -> None:
        self.message_service = service

        self._thread_pool.append(self.message_service)

    def set_input_service(self, input_listener_service: 'IInputListenerService',
                          input_controller_service: 'IInputControllerService',
                          screen_mouse_service: 'IServerScreenMouseService') -> None:

        self.listeners_service = input_listener_service
        self.controller_service = input_controller_service
        self.mouse_service = screen_mouse_service

        if isinstance(self.controller_service.get_mouse_controller(), IMouseController):
            self.current_mouse_position = self.controller_service.get_mouse_controller().get_current_position()

        self._thread_pool.append(self.listeners_service)
        self._thread_pool.append(self.controller_service)

    def set_file_transfer_service(self, file_transfer_service: 'IFileTransferService') -> None:
        self._file_transfer_service = file_transfer_service
        self._thread_pool.append(self.file_transfer_service)

    def set_transition_service(self, transition_service: 'IScreenTransitionController') -> None:
        self.screen_transition_controller = transition_service
        # Put as the first thread to start, it should be the first to stop
        self._thread_pool.insert(0, self.screen_transition_controller)

    def start(self):
        try:
            self._started = True

            # Threads initialization
            for thread in self._thread_pool:
                thread.start()

            self.log("Server started.", Logger.INFO)

        except Exception as e:
            self.log(f"Server not started: {e}", Logger.ERROR)
            return self.stop()

        return True

    def stop(self):
        if not self._started and not self.connection_service.is_alive():
            return True

        self.log("Server stopping ...", Logger.WARNING)
        self._started = False

        # --- Start cleanup ----

        try:

            # Wait for all threads to finish
            for thread in self._thread_pool:
                if thread.is_alive():
                    thread.join()

            # Recheck if all threads are stopped
            for thread in self._thread_pool:
                if thread.is_alive():
                    self.log(f"Thread {thread} is still alive.", Logger.WARNING)

            self.log("Server stopped.", Logger.INFO)
            return True
        except Exception as e:
            self.log(f"{e}", Logger.ERROR)
            return False

    """ --- Server Context --- """
    def is_running(self):
        with self._context_lock:
            return self._started

    def on_disconnect(self, conn):
        # Set client connection to None and change screen to Host (None)
        with self._context_lock:
            for key in self.clients.get_possible_positions():
                if self.clients.get_connection(key) == conn:
                    self.log(f"Client {key} disconnected.", Logger.WARNING)
                    self.clients.remove_connection(key)
                    self.event_bus.publish(self.event_bus.SCREEN_CHANGE_EVENT, None)
                    return

    def get_client(self, screen) -> IBaseSocket:
        with self._context_lock:
            return self.clients.get_connection(screen)

    def get_connected_clients(self):
        with self._context_lock:
            return self.clients.get_connected_clients()

    def change_screen(self, screen=None):
        self.screen_transition_controller.change_screen(screen)

    def reset_screen(self, direction: str, position: tuple):
        self.screen_transition_controller.reset_screen(direction, position)

    def mark_transition_changed(self):
        self.screen_transition_controller.mark_transition()

    def mark_transition_blocked(self):
        self.screen_transition_controller.mark_transition_blocked()

    def mark_transition_completed(self):
        self.screen_transition_controller.mark_transition_completed()

    def log(self, message, priority: int = 0):
        self.logger.log(message, priority)

    def get_clients(self):
        with self._context_lock:
            return self.clients

    def get_active_screen(self):
        with self._context_lock:
            return self.active_screen

    def is_transition_in_progress(self):
        with self._context_lock:
            return self.screen_transition_controller.is_transition_in_progress()

    def set_active_screen(self, screen: Optional[str]) -> None:
        with self._context_lock:
            self.active_screen = screen

    def is_transition_blocked(self) -> bool:
        with self._context_lock:
            return self.screen_transition_controller.is_transition_blocked()

    def get_current_mouse_position(self) -> tuple:
        if self.listeners_service:  # Get current virtual mouse position
            return self.listeners_service.get_mouse_position()
        elif self.mouse_controller:  # Fall back on real mouse position
            return self.mouse_controller.get_current_position()

    def has_client_position(self, screen: str) -> bool:
        with self._context_lock:
            return screen in self.clients.get_possible_positions()

    def is_client_connected(self, screen: str) -> bool:
        with self._context_lock:
            return self.clients.get_connection(screen) is not None

    def reset_mouse(self, direction: str, position: float):
        self.mouse_service.reset_mouse(direction, position)

    """ --- Controller Context --- """
    @property
    def mouse_controller(self) -> IMouseController:
        return self.controller_service.get_mouse_controller()

    @property
    def clipboard_controller(self) -> IClipboardController:
        return self.controller_service.get_clipboard_controller()

    @property
    def keyboard_controller(self) -> IKeyboardController:
        return self.controller_service.get_keyboard_controller()

    """ --- Server Screen Context --- """
    def get_screen_treshold(self) -> int:
        return self.screen_threshold

    def get_screen_size(self) -> tuple:
        return self.screen_width, self.screen_height

    """ --- File Transfer Context --- """
    @property
    def file_transfer_service(self) -> 'IFileTransferService':
        return self._file_transfer_service
