import logging
from attr import dataclass
from typing import Callable, Dict, Optional

from utils.Interfaces import IServerContext, IMessageService, IEventBus, IControllerContext, IFileTransferService, \
    IClientContext

from utils.net.netData import extract_command_parts


@dataclass
class Command:

    DESCRIPTION = "Default"

    logging.basicConfig(level=logging.DEBUG, format="[%(levelname)s][COMMAND] %(message)s")

    context: IServerContext | IClientContext | IControllerContext | IFileTransferService
    message_service: IMessageService
    event_bus: IEventBus
    screen: Optional[str]
    payload: Optional[list | tuple]

    def __repr__(self):
        return Command.DESCRIPTION

    def __str__(self):
        return Command.DESCRIPTION

    def execute(self):
        raise NotImplementedError("Subclasses should implement this!")


class CommandRegistry:
    def __init__(self):
        self.commands: Dict[str, Callable: Command] = {}

    def register(self, cmd_name: str, creator_fn: Callable):
        """
        Aggiunge un comando al registry. Esempio:
        registry.register("clipboard", lambda ctx, screen, payload: ClipboardCommand(ctx, payload))
        """
        self.commands[cmd_name] = creator_fn

    def create_command(self, cmd_name: str,
                       context: IServerContext | IClientContext | IControllerContext | IFileTransferService,
                       message_service: IMessageService, event_bus: IEventBus, screen: Optional[str],
                       payload: Optional[list | tuple]) -> Command | None:
        if cmd_name in self.commands:
            return self.commands[cmd_name](context=context, message_service=message_service,
                                           event_bus=event_bus, screen=screen, payload=payload)
        return None


class CommandFactory:
    registry = CommandRegistry()

    @staticmethod
    def create_command(raw_command: str | tuple,
                       context: IServerContext | IClientContext | IControllerContext | IFileTransferService,
                       message_service: IMessageService, event_bus: IEventBus,
                       screen: Optional[str] = None) -> Command | None:
        """
        raw_command: può essere una stringa o una tupla (comando già parsato).
        """
        if isinstance(raw_command, tuple):
            parts = raw_command
        else:
            parts = extract_command_parts(raw_command)

        cmd_name = parts[0]
        payload = parts[1:]
        return CommandFactory.registry.create_command(cmd_name=cmd_name, context=context,
                                                      message_service=message_service, event_bus=event_bus,
                                                      screen=screen, payload=payload)
