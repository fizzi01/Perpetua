import logging
from attr import dataclass
from typing import Callable, Dict, Optional

from utils.Interfaces import IServerContext, IMessageService, IEventBus, IControllerContext, IFileTransferService, \
    IClientContext, IBaseCommand

from utils.net.netData import extract_command_parts
from utils.command.CommandBuilder import CommandBuilder


@dataclass
class Command(IBaseCommand):
    """
    Base execution command class that also implements IBaseCommand interface.
    These are CommandExec-type commands that execute logic.
    """

    DESCRIPTION = "Default"

    logging.basicConfig(level=logging.DEBUG, format="[%(levelname)s][COMMAND] %(message)s")

    context: IServerContext | IClientContext | IControllerContext | IFileTransferService
    message_service: IMessageService
    event_bus: IEventBus
    screen: Optional[str]
    payload: Optional[list | tuple]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Extract fields specific to Command
        self.context = kwargs.get('context', None)
        self.message_service = kwargs.get('message_service', None)
        self.event_bus = kwargs.get('event_bus', None)
        self.screen = kwargs.get('screen', None)
        self.payload = kwargs.get('payload', None)

    def __repr__(self):
        return self.DESCRIPTION

    def __str__(self):
        return self.DESCRIPTION

    def to_protocol_message(self, source: Optional[str] = None, target: Optional[str] = None):
        """Default implementation - subclasses should override if needed."""
        raise NotImplementedError("Execution commands should implement this if they need protocol conversion!")

    def to_legacy_string(self) -> str:
        """Default implementation - subclasses should override if needed."""
        if self.payload:
            return f"{self.DESCRIPTION} {' '.join(str(p) for p in self.payload)}"
        return self.DESCRIPTION

    @classmethod
    def from_legacy_string(cls, command_str: str, **kwargs):
        """Default implementation - subclasses should override if needed."""
        parts = extract_command_parts(command_str)
        if parts and parts[0] == cls.DESCRIPTION:
            return cls(payload=parts[1:], **kwargs)
        return None

    def execute(self):
        raise NotImplementedError("Subclasses should implement this!")


class CommandRegistry:
    def __init__(self):
        self.commands: Dict[str, Callable] = {}

    def register(self, cmd_name: str, creator_fn: Callable):
        """
        Register a command creator function.
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

    def create_command_from_base_command(self, base_command: IBaseCommand,
                                         context: IServerContext | IClientContext | IControllerContext | IFileTransferService,
                                         message_service: IMessageService, event_bus: IEventBus) -> Command | None:
        """
        Create execution command from IBaseCommand object.
        """
        cmd_name = base_command.DESCRIPTION
        if cmd_name in self.commands:
            # Try calling with base_command parameter first (new style)
            try:
                return self.commands[cmd_name](
                    context=context,
                    message_service=message_service,
                    event_bus=event_bus,
                    screen=base_command.screen,
                    payload=None,
                    base_command=base_command
                )
            except TypeError:
                # Fall back to legacy style if lambda doesn't accept base_command
                return self.commands[cmd_name](
                    context=context,
                    message_service=message_service,
                    event_bus=event_bus,
                    screen=base_command.screen,
                    payload=None
                )
        return None


class CommandFactory:
    registry = CommandRegistry()

    @staticmethod
    def create_command(raw_command: str | tuple | IBaseCommand,
                       context: IServerContext | IClientContext | IControllerContext | IFileTransferService,
                       message_service: IMessageService, event_bus: IEventBus,
                       screen: Optional[str] = None) -> Command | None:
        """
        Create command from various input types: string, tuple, or IBaseCommand object.
        """
        
        # If it's already an IBaseCommand object, use it directly
        if isinstance(raw_command, IBaseCommand):
            return CommandFactory.registry.create_command_from_base_command(
                raw_command, context, message_service, event_bus
            )
        
        # Legacy string/tuple processing
        if isinstance(raw_command, tuple):
            parts = raw_command
        else:
            # Try to parse as IBaseCommand first
            base_command = CommandBuilder.from_legacy_string(raw_command)
            if base_command:
                base_command.screen = screen  # Set screen context
                return CommandFactory.registry.create_command_from_base_command(
                    base_command, context, message_service, event_bus
                )
            
            # Fall back to legacy parsing
            parts = extract_command_parts(raw_command)

        cmd_name = parts[0] if parts else ""
        payload = parts[1:] if len(parts) > 1 else []
        return CommandFactory.registry.create_command(cmd_name=cmd_name, context=context,
                                                      message_service=message_service, event_bus=event_bus,
                                                      screen=screen, payload=payload)
