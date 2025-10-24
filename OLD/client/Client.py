import threading
from typing import Optional, Dict, Any

from utils.Logging import Logger
from utils import screen_size
from utils.Interfaces import IClientContext, IScreenContext, IControllerContext, IFileTransferContext, IClient, \
    IClientSocket, IClientInfoObj, IServerConnectionService, IClientStateService, State, IInputControllerService, \
    IKeyboardController, IClipboardController, IMouseController


class Client(IClientContext, IScreenContext, IControllerContext, IFileTransferContext, IClient):
    def __init__(self, client_info: IClientInfoObj, screen_threshold: int = 1,
                 screen: Optional[tuple] = None, logger: Logger = None):

        self.client_info: 'IClientInfoObj' = client_info

        self.screen_threshold = screen_threshold
        self.screen_size = screen if screen else screen_size()

        self.state: Optional[IClientStateService] = None
        self.connection_service: Optional[IServerConnectionService] = None
        self.message_service: Optional['IMessageService'] = None

        self.listener_service: Optional['IInputListenerService'] = None
        self.controller_service: Optional[IInputControllerService] = None

        self._file_transfer_service: Optional['IFileTransferService'] = None

        self.event_bus: Optional['IEventBus'] = None

        self.logger = logger

        self._thread_pool = []
        self._started = False  # Main variable for client status, if False the client is stopped automatically
        self.lock = threading.Lock()
        self._running = False

    def set_client_state_service(self, service: 'IClientStateService') -> None:
        self.state = service

    def set_connection_service(self, connection_service: 'IServerConnectionService') -> None:
        self.connection_service = connection_service
        self._thread_pool.append(self.connection_service)

    def set_message_service(self, service: 'IMessageService') -> None:
        self.message_service = service
        self._thread_pool.append(self.message_service)

    def set_input_service(self, input_listener_service: 'IInputListenerService',
                          input_controller_service: 'IInputControllerService') -> None:
        self.listener_service = input_listener_service
        self.controller_service = input_controller_service

        self._thread_pool.extend([self.listener_service, self.controller_service])

    def set_file_transfer_service(self, service: 'IFileTransferService') -> None:
        self._file_transfer_service = service
        self._thread_pool.append(self.file_transfer_service)

    def set_event_bus(self, event_bus: 'IEventBus') -> None:
        self.event_bus = event_bus
        self._thread_pool.append(self.event_bus)

    def start(self):
        try:
            if not self._running:
                self._running = True

                for thread in self._thread_pool:
                    thread.start()

                self.log("Client started.", Logger.INFO)
            else:
                raise Exception("Client already started.")

        except Exception as e:
            self.log(f"{e}", Logger.ERROR)
            return self.stop()

        return True

    def stop(self):
        try:

            if not self._running and not self.connection_service.is_alive():
                return True

            self.log("Stopping client...", Logger.WARNING)
            self._running = False

            for thread in self._thread_pool:
                if thread.is_alive():
                    thread.join()

            # Recheck if the client is still running
            for thread in self._thread_pool:
                if thread.is_alive():
                    raise Exception("Failed to stop client.")

            self.log("Client stopped.", Logger.INFO)
            return True
        except Exception as e:
            self.log(f"{e}", 2)
            return False

    def get_server_screen_size(self):
        return self.client_info.server_screen_size

    def get_connected_server(self) -> IClientSocket:
        return self.connection_service.socket

    def get_client_info(self) -> Dict[str, Any]:
        return self.client_info.serialize()

    def get_client_info_obj(self) -> IClientInfoObj:
        return self.client_info

    def set_client_info_obj(self, client_info: IClientInfoObj) -> None:
        self.client_info = client_info

    def get_client_screen_size(self) -> tuple:
        return self.screen_size

    def get_key_map(self) -> Dict[str, str]:
        return self.client_info.key_map

    def get_screen_size(self) -> tuple:
        return self.screen_size

    def set_state(self, state: State) -> None:
        self.state.set_state(state)

    def get_state(self) -> bool:
        return self.state.get_state()

    def is_state(self, state: State.__class__) -> bool:
        return self.state.is_state(state)

    def log(self, message: str, priority: int = 0) -> None:
        self.logger.log(message, priority)

    def get_screen_treshold(self) -> int:
        return self.screen_threshold

    @property
    def mouse_controller(self) -> IMouseController:
        return self.controller_service.get_mouse_controller()

    @property
    def clipboard_controller(self) -> IClipboardController:
        return self.controller_service.get_clipboard_controller()

    @property
    def keyboard_controller(self) -> IKeyboardController:
        return self.controller_service.get_keyboard_controller()

    @property
    def file_transfer_service(self) -> 'IFileTransferService':
        return self._file_transfer_service





