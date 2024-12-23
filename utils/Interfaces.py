# interfaces.py
from threading import Thread
from abc import ABC, abstractmethod
from typing import Protocol, List, Optional, Any, Dict, Callable, runtime_checkable
from socket import socket

from attr import dataclass


class IHandler(Protocol):

    def start(self):
        ...

    def stop(self):
        ...

    def is_alive(self) -> bool:
        ...


class IMouseListener(IHandler):

    def get_position(self):
        """
        Return current virtual mouse position
        :return:
        """
        ...


class IMouseController(IHandler):

    def process_mouse_command(self, x: str | int | float, y: str | int | float, mouse_action: str,
                              is_pressed: bool) -> None:
        ...

    def get_current_position(self) -> tuple:
        ...

    def set_position(self, x: int | float, y: int | float) -> None:
        ...

    def move(self, x: int | float, y: int | float) -> None:
        ...


class IClipboardController(IHandler):
    def get_clipboard_data(self) -> str:
        ...

    def set_clipboard_data(self, data: str) -> None:
        ...


class IKeyboardController(IHandler):

    def process_key_command(self, key_data: str, key_action: str) -> None:
        ...


class IEventBus(Thread):
    SCREEN_CHANGE_EVENT = "SCREEN_CHANGE"
    SCREEN_RESET_EVENT = "SCREEN_RESET"
    CHANGE_STATE_EVENT = "CHANGE_STATE"

    def subscribe(self, event: str, handler: Callable) -> None:
        ...

    def start(self) -> None:
        ...

    def join(self, timeout: int = 0) -> None:
        ...

    def publish(self, event: str, *args, **kwargs) -> None:
        ...


class IClientConnectionHandler(ABC):
    @abstractmethod
    def handle_connection(self, conn: socket, addr: tuple, clients: 'IClients'):
        pass

    @abstractmethod
    def stop(self):
        pass

    @abstractmethod
    def check_client_connections(self):
        pass

    @abstractmethod
    def is_client_connected(self, address: tuple):
        pass

    @abstractmethod
    def exchange_configuration(self, conn: socket):
        pass


class IMessageQueueManager(Protocol):
    def join(self, timeout: Optional[float] = None) -> None:
        ...

    def is_alive(self) -> bool:
        ...

    def start(self) -> None:
        ...

    def send(self, priority: int, message: Any) -> None:
        ...


@runtime_checkable
class IBaseSocket(Protocol):

    @property
    def address(self) -> str:
        ...

    def send(self, data: str | bytes) -> None:
        ...

    def recv(self, size: int) -> bytes | str:
        ...

    def close(self) -> None:
        ...

    def is_socket_open(self) -> bool:
        ...


class IServerSocket(Protocol):
    def bind_and_listen(self) -> None:
        ...

    def accept(self) -> tuple[socket, tuple]:
        ...

    def pause(self) -> None:
        ...

    def close(self) -> None:
        ...

    def get_host(self) -> str:
        ...

    def get_port(self) -> int:
        ...

    def is_socket_open(self) -> bool:
        ...

    def _resolve_port_conflict(self) -> None:
        ...

    def _is_port_in_use_by_mdns(self, port: int) -> bool:
        ...

    def _register_mdns_service(self) -> None:
        ...

    def _unregister_mdns_service(self) -> None:
        ...


class IServer(Protocol):

    def start(self) -> None:
        ...

    def stop(self) -> None:
        ...

    def set_connection_service(self, connection_service: 'IServerConnectionService') -> None:
        ...

    def set_message_service(self, service: 'IMessageService') -> None:
        ...

    def set_input_service(self, input_listener_service: 'IInputListenerService',
                          input_controller_service: 'IInputControllerService',
                          screen_mouse_service: 'IServerScreenMouseService') -> None:
        ...

    def set_transition_service(self, transition_service: 'IScreenTransitionController') -> None:
        ...

    def set_file_transfer_service(self, file_transfer_service: 'IFileTransferService') -> None:
        ...

    def set_event_bus(self, event_bus: 'IEventBus') -> None:
        ...


class IScreenContext(Protocol):
    def get_screen_size(self) -> tuple:
        ...

    def get_screen_treshold(self) -> int:
        ...


@runtime_checkable
class IServerContext(Protocol):
    def is_running(self) -> bool:
        ...

    def get_active_screen(self) -> Optional[str]:
        """
        Get the current active screen (Client position)
        :return: Screen position
        """
        ...

    def set_active_screen(self, screen: Optional[str]) -> None:
        ...

    def get_connected_clients(self) -> List[str]:
        ...

    def get_client(self, screen: str) -> Optional['IBaseSocket']:
        ...

    def get_clients(self) -> 'IClients':
        ...

    def is_transition_in_progress(self) -> bool:
        ...

    def is_transition_blocked(self) -> bool:
        ...

    def reset_mouse(self, direction: str, position: float) -> None:
        ...

    def get_current_mouse_position(self) -> tuple:
        """
        Get the current virtual mouse position
        :return: Tuple of x, y coordinates
        """
        ...

    def has_client_position(self, screen: str) -> bool:
        """
        Check if the screen has a client connected
        """
        ...

    def is_client_connected(self, screen: str) -> bool:
        """
        Check if the client in screen position is connected
        """
        ...

    def on_disconnect(self, conn: socket) -> None:
        ...

    def change_screen(self, screen: Optional[str] = None) -> None:
        ...

    def reset_screen(self, direction: str, position: tuple | None) -> None:
        """
        Reset server screen from direction in a specific position
        :param direction: From which client screen to reset (left, right, ...)
        :param position: Position to reset the screen cursor
        :return:
        """
        ...

    def mark_transition_changed(self) -> None:
        ...

    def mark_transition_completed(self) -> None:
        ...

    def mark_transition_blocked(self) -> None:
        ...

    def log(self, message: str, priority: int = 0) -> None:
        ...


@runtime_checkable
class IControllerContext(Protocol):

    @property
    def mouse_controller(self) -> IMouseController: ...

    @property
    def clipboard_controller(self) -> IClipboardController: ...

    @property
    def keyboard_controller(self) -> IKeyboardController: ...


@runtime_checkable
class IFileTransferContext(Protocol):
    @property
    def file_transfer_service(self) -> 'IFileTransferService': ...


class IMessageService(Thread):
    def send_mouse(self, screen: str | None, message: str) -> None:
        ...

    def send_keyboard(self, screen: str | None, message: str) -> None:
        ...

    def send_clipboard(self, screen: str | None, message: str) -> None:
        ...

    def send_screen_notification(self, screen: str | None, message: str) -> None:
        ...

    def send_file_request(self, screen: str | None, message: str) -> None:
        ...

    def send_file_copy(self, screen: str | None, message: str) -> None:
        ...

    def send_file(self, file_path: str, screen: str) -> None:
        ...

    def forward_file_data(self, screen: str, data: str) -> None:
        ...

    def send(self, priority: int, message) -> None:
        ...


class IClientObj(Protocol):
    def get_screen_size(self) -> tuple:
        ...

    def set_screen_size(self, size: tuple):
        ...

    def get_connection(self) -> Optional[IBaseSocket]:
        ...

    def get_address(self) -> str:
        ...

    def get_port(self) -> int:
        ...

    def get_key_map(self) -> Dict[str, str]:
        ...

    def set_key_map(self, key_map: Dict[str, str]):
        ...

    def get_key(self, key: str) -> str:
        ...

    def set_connection(self, conn: Optional[IBaseSocket]):
        ...

    def set_address(self, addr: str):
        ...

    def set_port(self, port: int):
        ...

    def is_connected(self) -> bool:
        ...


class IClients(Protocol):

    def get_client(self, position: str) -> Optional[IClientObj]:
        ...

    def set_client(self, position: str, client: IClientObj):
        ...

    def get_connection(self, position: str) -> Optional[IBaseSocket]:
        ...

    def set_connection(self, position: str, conn: IBaseSocket):
        ...

    def set_screen_size(self, position: str, size: tuple):
        ...

    def get_screen_size(self, position: str) -> tuple:
        ...

    def remove_connection(self, position: str):
        ...

    def get_possible_positions(self):
        ...

    def get_address(self, position: str) -> str:
        ...

    def get_position_by_address(self, addr: str) -> Optional[str]:
        ...

    def set_address(self, position: str, addr: str):
        ...

    def remove_client(self, position: str):
        ...

    def get_connected_clients(self) -> Dict[str, IClientObj]:
        ...


class IScreenTransitionController(Thread):
    def start(self) -> None:
        ...

    def join(self, timeout: int = 0) -> None:
        ...

    def is_alive(self):
        ...

    def change_screen(self, screen: Optional[str]) -> None:
        ...

    def reset_screen(self, direction: str, position: tuple | None) -> None:
        """
        Reset server screen from direction in a specific position
        :param direction: From which client screen to reset (left, right, ...)
        :param position: Position to reset the screen cursor
        :return:
        """
        ...

    def mark_transition(self):
        ...

    def mark_transition_completed(self):
        ...

    def mark_transition_blocked(self):
        ...

    def is_transition_in_progress(self) -> bool:
        ...

    def is_transition_blocked(self) -> bool:
        ...


class IService(Thread):

    def start(self) -> None:
        ...

    def join(self, timeout: int = 0) -> None:
        ...

    def stop(self) -> None:
        ...

    def is_alive(self) -> bool:
        ...


class IInputListenerService(IService):

    def get_mouse_position(self) -> tuple:
        """
        Get the current virtual mouse position
        :return: Tuple of x, y coordinates
        """
        ...


class IInputControllerService(IService):

    def get_mouse_controller(self) -> IMouseController | None:
        ...

    def get_clipboard_controller(self) -> IClipboardController | None:
        ...

    def get_keyboard_controller(self) -> IKeyboardController | None:
        ...


class IServerScreenMouseService(IService):

    def reset_mouse(self, direction: str, pos: float):
        ...


class IFileTransferService(IService):
    LOCAL_OWNERSHIP = "local_client"
    LOCAL_SERVER_OWNERSHIP = "local_server"
    EXTERNAL_OWNERSHIP = "external"

    SERVER_REQUEST = "server"
    CLIENT_REQUEST = "client"

    def set_save_path(self, save_path: str):
        ...

    def get_save_path(self) -> str:
        ...

    def handle_file_paste(self, file_path: str):
        ...

    def handle_file_copy(self, file_name: str, file_size: int, file_path: str,
                         local_owner: str | None = None, caller_screen: str | None = None):
        ...

    def handle_file_copy_external(self, file_name: str, file_size: int, file_path: str):
        ...

    def handle_file_request(self, requester_screen: str):
        ...

    def handle_file_start(self, from_screen: str, file_name: str, file_size: int):
        ...

    def handle_file_chunk(self, from_screen: str, encoded_chunk: str, chunk_index: int):
        ...

    def handle_file_end(self, from_screen: str):
        ...


class IClientHandler(Protocol):
    def start(self):
        ...

    def stop(self):
        ...

    def is_alive(self) -> bool:
        ...


class IClientHandlerFactory(Protocol):
    def create_handler(self, conn: IBaseSocket, screen: str,
                       process_command: Callable[[str | tuple, str], None]) -> IClientHandler:
        ...


@dataclass
class IClientCommandProcessor(Protocol):
    context: IServerContext | IControllerContext | IFileTransferService
    message_service: 'IMessageService'
    event_bus: 'IEventBus'
    logger: Callable

    def process_client_command(self, command: str | tuple, screen: str | None) -> None:
        ...


# CLIENT SPECIFIC

class State(ABC):
    @abstractmethod
    def handle(self):
        pass


class IClientStateService(Protocol):
    def set_state(self, state: State) -> None:
        ...

    def get_state(self) -> bool:
        ...

    def is_state(self, state: State.__class__) -> bool:
        ...

    def is_controlled(self) -> bool:
        ...


class IClientSocket(IBaseSocket):
    host: str
    port: int
    wait: int
    use_ssl: bool

    def connect(self) -> None:
        ...

    def reset_socket(self) -> None:
        ...


@dataclass
class IClientInfoObj(Protocol):
    address: str
    port: int
    screen_size: tuple
    server_screen_size: Optional[str | tuple]
    key_map: Optional[Dict[str, str]]

    def serialize(self) -> Dict[str, Any]:
        # Construct dict
        return {
            "address": self.address,
            "port": self.port,
            "screen_size": self.screen_size,
            "server_screen_size": self.server_screen_size,
            "key_map": self.key_map
        }


@runtime_checkable
class IClient(Protocol):
    def start(self) -> None:
        ...

    def stop(self) -> None:
        ...

    def set_client_state_service(self, service: 'IClientStateService') -> None:
        ...

    def set_connection_service(self, connection_service: 'IServerConnectionService') -> None:
        ...

    def set_message_service(self, service: 'IMessageService') -> None:
        ...

    def set_input_service(self, input_listener_service: 'IInputListenerService',
                          input_controller_service: 'IInputControllerService') -> None:
        ...

    def set_transition_service(self, transition_service: 'IScreenTransitionController') -> None:
        ...

    def set_file_transfer_service(self, service: 'IFileTransferService') -> None:
        ...

    def set_event_bus(self, event_bus: 'IEventBus') -> None:
        ...


@runtime_checkable
class IClientContext(Protocol):

    def get_connected_server(self) -> IClientSocket:
        ...

    def get_client_info(self) -> Dict[str, Any]:
        ...

    def get_client_info_obj(self) -> IClientInfoObj:
        ...

    def set_client_info_obj(self, client_info: IClientInfoObj) -> None:
        ...

    def get_client_screen_size(self) -> tuple:
        ...

    def get_server_screen_size(self) -> str:
        ...

    def get_key_map(self) -> Dict[str, str]:
        ...

    def get_screen_size(self) -> tuple:
        ...

    def set_state(self, state: State) -> None:
        ...

    def get_state(self) -> bool:
        ...

    def is_state(self, state: State.__class__) -> bool:
        ...

    def log(self, message: str, priority: int = 0) -> None:
        ...


class IInputListenerFactory(Protocol):
    def create_mouse_listener(self, context: IServerContext | IClientContext, message_service: IMessageService,
                              event_bus: IEventBus,
                              screen_width: int, screen_height: int, screen_threshold: int) -> IHandler:
        ...

    def create_keyboard_listener(self, context: IServerContext | IClientContext,
                                 message_service: IMessageService,
                                 event_bus: IEventBus) -> IHandler:
        ...

    def create_clipboard_listener(self, context: IServerContext | IClientContext,
                                  message_service: IMessageService,
                                  event_bus: IEventBus) -> IHandler:
        ...


class IInputControllerFactory(Protocol):
    def create_mouse_controller(self, context: IServerContext | IClientContext,
                                message_service: IMessageService) -> IHandler | IMouseController:
        ...

    def create_keyboard_controller(self, context: IServerContext | IClientContext,
                                   message_service: IMessageService) -> IHandler | IKeyboardController:
        ...

    def create_clipboard_controller(self, context: IServerContext | IClientContext,
                                    message_service: IMessageService) -> IHandler | IClipboardController:
        ...


class IServerCommandProcessor(Protocol):

    def __init__(self, context: IClientContext | IControllerContext | IFileTransferService,
                 message_service: IMessageService, event_bus: IEventBus):
        self.context = context
        self.message_service = message_service
        self.event_bus = event_bus
        self.logger = context.log

    # TODO: Use same interface as IClientCommandProcessor and GENERALIZE THEM :D
    def process_server_command(self, command: str | tuple, screen: str | None = None) -> None:
        ...


class IServerHandler(Protocol):
    def start(self):
        ...

    def stop(self):
        ...

    def is_alive(self) -> bool:
        ...


class IServerHandlerFactory(Protocol):

    # TODO: Generalize with the ClientHandlerFactory
    def create_handler(self, conn: IBaseSocket | IClientSocket,
                       process_command: Callable[[str], None]) -> IServerHandler:
        """
        Create a new server handler (client context)
        :param conn: Connected Socket
        :param process_command: Function to process commands
        :return: IServerHandler
        """
        ...


class IServerConnectionHandler(Protocol):

    @abstractmethod
    def handle_connection(self):
        pass

    @abstractmethod
    def add_server_connection(self):
        pass

    def stop(self):
        ...

    def exchange_configurations(self) -> bool:
        ...

    def check_server_connection(self) -> bool:
        ...


class IConnectionHandlerFactory(Protocol):

    @staticmethod
    def create_handler(ssl_enabled: bool, certfile: Optional[str] = None,
                       context: Optional[IServerContext | IClientContext] = None,
                       keyfile: Optional[str] = None,
                       handler_socket: Optional[IBaseSocket | IServerSocket | IClientSocket] = None,
                       command_processor: Callable[[str | tuple, str], None] = None,
                       handler_factory: IClientHandlerFactory | IServerHandlerFactory = None) -> (
            IClientConnectionHandler
            | IServerConnectionHandler):
        ...


@dataclass
class IServerConnectionService(IService):
    socket: Optional[IBaseSocket | IServerSocket | IClientSocket] = None

    def start(self) -> bool:
        """
         Start the server connection service
         :return: True if the service started successfully, False otherwise
         """
        ...

    def join(self, timeout: float = 5):
        """
        Stop the server connection service
        :param timeout: Timeout in seconds
        :return: None
        """
        ...

    def is_alive(self) -> bool:
        """
        Check if the service is running
        :return: True if the service is running, False otherwise
        """
        ...
