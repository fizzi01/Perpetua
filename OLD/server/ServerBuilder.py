from config.ServerConfig import Clients, Client

from inputUtils.FileTransferService import FileTransferService

from network.IOManager import ServerMessageQueueManager, MessageService
from network.ServerSocket import ServerSocket, ConnectionHandlerFactory

from server import Server
from server.connections.ClientHandler import ClientCommandProcessor, ClientHandlerFactory
from server.connections.ConnectionService import ConnectionService
from server.input.InputService import InputListenerService, InputControllerService, ScreenMouseService
from server.screen.ScreenTransition import ScreenTransitionController

from utils.Events import EventBus
from utils.Logging import Logger
from utils import screen_size

from inputUtils.HandlerFactory import ServerInputControllerFactory, ServerInputListenerFactory


class ServerBuilder:
    def __init__(self):
        self.host = "0.0.0.0"
        self.port = 5001
        self.clients = None
        self.wait = 5
        self.logging = False
        self.stdout = print
        self.screen_threshold = 10
        self.use_ssl = False
        self.certfile = None
        self.keyfile = None

    def set_host(self, host: str):
        self.host = host if len(host) > 0 else "0.0.0.0"
        return self

    def set_port(self, port: int):
        self.port = port
        return self

    def set_clients(self, clients: Clients):
        self.clients = clients
        return self

    def set_wait(self, wait: int):
        self.wait = wait
        return self

    def set_logging(self, logging: bool, stdout=print):
        self.logging = logging
        self.stdout = stdout
        return self

    def set_screen_threshold(self, threshold: int):
        self.screen_threshold = threshold
        return self

    def enable_ssl(self, certfile: str, keyfile: str):
        self.use_ssl = True
        self.certfile = certfile
        self.keyfile = keyfile
        return self

    def build(self) -> Server:
        # Creazione logger
        logger = Logger(self.logging, self.stdout)

        # Se clients è None ne creiamo uno default
        if self.clients is None:
            self.clients = Clients({"left": Client()})

        # Creazione ServerSocket
        server_socket = ServerSocket(self.host, self.port, self.wait)

        screen_width, screen_height = screen_size()

        server = Server(
            logger=logger,
            clients=self.clients,
            server_socket=server_socket,
            screen_threshold=self.screen_threshold,
            screen_width=screen_width,
            screen_height=screen_height
        )

        # Creazione manager messaggi
        messages_manager = ServerMessageQueueManager(server)
        message_service = MessageService(messages_manager)
        server.set_message_service(service=message_service)

        # Event bus
        event_bus = EventBus()
        event_bus.subscribe(EventBus.SCREEN_CHANGE_EVENT, lambda new_screen: server.change_screen(new_screen))
        event_bus.subscribe(EventBus.SCREEN_RESET_EVENT, lambda direction, position: server.reset_screen(direction=direction, position=position))
        server.set_event_bus(event_bus)

        # Input services
        mouse_listener = ServerInputListenerFactory().create_mouse_listener(context=server,
                                                                            message_service=message_service,
                                                                            event_bus=event_bus,
                                                                            screen_width=screen_width,
                                                                            screen_height=screen_height,
                                                                            screen_threshold=self.screen_threshold)
        keyboard_listener = ServerInputListenerFactory().create_keyboard_listener(context=server,
                                                                                  event_bus=event_bus,
                                                                                  message_service=message_service)
        clipboard_listener = ServerInputListenerFactory().create_clipboard_listener(context=server,
                                                                                    event_bus=event_bus,
                                                                                    message_service=message_service)

        server_input_service = InputListenerService(context=server,
                                                    mouse_listener=mouse_listener,
                                                    keyboard_listener=keyboard_listener,
                                                    clipboard_listener=clipboard_listener,
                                                    logger=logger)

        mouse_controller = ServerInputControllerFactory().create_mouse_controller(context=server,
                                                                                  message_service=message_service)
        clipboard_controller = ServerInputControllerFactory().create_clipboard_controller(context=server,
                                                                                          message_service=message_service)
        # Actually None, because we don't have a specific controller for keyboard
        keyboard_controller = ServerInputControllerFactory().create_keyboard_controller(context=server,
                                                                                        message_service=message_service)

        server_input_controller_service = InputControllerService(mouse_controller=mouse_controller,
                                                                 clipboard_controller=clipboard_controller,
                                                                 keyboard_controller=keyboard_controller)

        screen_mouse_service = ScreenMouseService(server)

        server.set_input_service(input_listener_service=server_input_service,
                                 input_controller_service=server_input_controller_service,
                                 screen_mouse_service=screen_mouse_service)

        # --- File transfer ---
        file_service = FileTransferService(message_service=message_service, context=server)
        server.set_file_transfer_service(file_service)

        # --- Connection services ---
        # Command processor needed to process client commands
        command_processor = ClientCommandProcessor(context=server,
                                                   logger=logger.log,
                                                   event_bus=event_bus,
                                                   message_service=message_service)

        client_handler_factory = ClientHandlerFactory()
        # Creazione connection_handler
        connection_handler = ConnectionHandlerFactory.create_handler(
            ssl_enabled=self.use_ssl,
            certfile=self.certfile,
            keyfile=self.keyfile,
            command_processor=lambda cmd, scr: command_processor.process_client_command(cmd, scr),
            handler_factory=client_handler_factory,
        )
        connection_service = ConnectionService(server_socket, self.clients, connection_handler, server, logger)
        server.set_connection_service(connection_service)

        # ---- Screen transition ----
        screen_transition_service = ScreenTransitionController(context=server)
        server.set_transition_service(screen_transition_service)

        # ---- Commands Initialization ----
        from server.command import register_commands
        register_commands()

        return server
