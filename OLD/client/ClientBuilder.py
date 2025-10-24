from client import ClientState, ServerHandlerFactory

from client.Client import Client
from client.command.CommandProcessor import ServerCommandProcessor
from client.connections.ConnectionService import ConnectionService
from client.input.InputService import InputListenerService, InputControllerService
from config.ClientConfig import ClientInfo
from inputUtils.HandlerFactory import ClientInputListenerFactory, ClientInputControllerFactory
from network.IOManager import ClientMessageQueueManager, MessageService
from network.ClientSocket import ClientSocket, ConnectionHandlerFactory
from utils.Logging import Logger
from utils.Events import EventBus
from inputUtils.FileTransferService import FileTransferService

from utils import screen_size


class ClientBuilder:
    def __init__(self):
        self.host = ""
        self.port = 5001
        self.use_ssl = False
        self.certfile = None
        self.keyfile = None
        self.logging = False
        self.screen_treshold = 1
        self.wait = 5
        self.stdout = print
        self.logging = False

    def set_screen_treshold(self, screen_treshold: int):
        self.screen_treshold = screen_treshold
        return self

    def set_wait(self, wait: int):
        self.wait = wait
        return self

    def set_host(self, host: str):
        self.host = host if host else ""
        return self

    def set_port(self, port: int):
        self.port = port
        return self

    def enable_ssl(self, certfile: str, keyfile: str):
        self.use_ssl = True
        self.certfile = certfile
        self.keyfile = keyfile
        return self

    def set_logging(self, logging: bool, stdout=print):
        self.logging = logging
        self.stdout = stdout
        return self

    def build(self) -> Client:
        # Initialize logger
        logger = Logger(self.logging, self.stdout)

        # Create ClientSocket
        client_socket = ClientSocket(host=self.host, port=self.port, use_ssl=self.use_ssl,
                                     certfile=self.certfile, wait=self.wait)

        # Create Client instance
        width, height = screen_size()
        client_info = ClientInfo(address=self.host, port=self.port, screen_size=(width, height),
                                 server_screen_size=None, key_map=None)
        client = Client(screen_threshold=self.screen_treshold,
                        screen=(width, height),
                        logger=logger,
                        client_info=client_info)

        client_state = ClientState()
        client.set_client_state_service(service=client_state)

        # Create EventBus
        event_bus = EventBus()
        event_bus.subscribe(EventBus.CHANGE_STATE_EVENT, lambda state: client_state.set_state(state))
        client.set_event_bus(event_bus)

        # Create IO Managers
        messages_manager = ClientMessageQueueManager(client)
        message_service = MessageService(messages_manager)
        client.set_message_service(service=message_service)

        # Set up File Transfer Service
        file_transfer_service = FileTransferService(message_service=message_service, context=client)
        client.set_file_transfer_service(service=file_transfer_service)

        # Input Services
        mouse_listener = (ClientInputListenerFactory()
                          .create_mouse_listener(context=client, message_service=message_service,
                                                 event_bus=event_bus, screen_width=width, screen_height=height,
                                                 screen_threshold=self.screen_treshold))
        keyboard_listener = (ClientInputListenerFactory()
                             .create_keyboard_listener(context=client,
                                                       message_service=message_service,
                                                       event_bus=event_bus))
        clipboard_listener = (ClientInputListenerFactory()
                              .create_clipboard_listener(context=client,
                                                         message_service=message_service,
                                                         event_bus=event_bus))

        client_input_service = InputListenerService(context=client,
                                                    mouse_listener=mouse_listener,
                                                    keyboard_listener=keyboard_listener,
                                                    clipboard_listener=clipboard_listener,
                                                    logger=logger)

        # Controller Service
        mouse_controller = (ClientInputControllerFactory()
                            .create_mouse_controller(context=client, message_service=message_service))
        keyboard_controller = (
            ClientInputControllerFactory().create_keyboard_controller(context=client, message_service=message_service))
        clipboard_controller = (
            ClientInputControllerFactory().create_clipboard_controller(context=client, message_service=message_service))

        client_controller_service = InputControllerService(mouse_controller=mouse_controller,
                                                           clipboard_controller=clipboard_controller,
                                                           keyboard_controller=keyboard_controller)

        client.set_input_service(input_listener_service=client_input_service,
                                 input_controller_service=client_controller_service)

        # Create Command Processor
        command_processor = ServerCommandProcessor(
            context=client,
            message_service=message_service,
            event_bus=event_bus
        )

        # Create ClientHandlerFactory
        client_handler_factory = ServerHandlerFactory()

        # Create ConnectionHandler
        connection_handler = ConnectionHandlerFactory.create_handler(
            ssl_enabled=self.use_ssl,
            command_processor=command_processor.process_server_command,
            handler_factory=client_handler_factory,
            handler_socket=client_socket,
            context=client,
        )

        connection_service = ConnectionService(client_socket, connection_handler, client, logger=logger)
        client.set_connection_service(connection_service)

        # Start all services
        client.start()

        # ---- Commands Initialization ----
        from client.command import register_commands
        register_commands()

        return client
