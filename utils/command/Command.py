import logging
from attr import dataclass
from typing import Callable, Dict, Optional, Union

from utils.Interfaces import IServerContext, IMessageService, IEventBus, IControllerContext, IFileTransferService, \
    IClientContext
from utils.data import IDataObject

from utils.net.netData import extract_command_parts


@dataclass
class Command:

    DESCRIPTION = "Default"

    logging.basicConfig(level=logging.DEBUG, format="[%(levelname)s][COMMAND] %(message)s")

    context: IServerContext | IClientContext | IControllerContext | IFileTransferService
    message_service: IMessageService
    event_bus: IEventBus
    screen: Optional[str]
    payload: Optional[list | tuple]  # Legacy support
    data_object: Optional[IDataObject] = None  # New structured data object

    def __repr__(self):
        return Command.DESCRIPTION

    def __str__(self):
        return Command.DESCRIPTION

    def execute(self):
        raise NotImplementedError("Subclasses should implement this!")
    
    def has_data_object(self) -> bool:
        """Check if command has structured data object."""
        return self.data_object is not None
    
    def has_legacy_payload(self) -> bool:
        """Check if command has legacy payload."""
        return self.payload is not None


class CommandRegistry:
    def __init__(self):
        self.commands: Dict[str, Callable] = {}

    def register(self, cmd_name: str, creator_fn: Callable):
        """
        Aggiunge un comando al registry. Esempio:
        registry.register("clipboard", lambda ctx, screen, payload: ClipboardCommand(ctx, payload))
        """
        self.commands[cmd_name] = creator_fn

    def create_command(self, cmd_name: str,
                       context: IServerContext | IClientContext | IControllerContext | IFileTransferService,
                       message_service: IMessageService, event_bus: IEventBus, screen: Optional[str],
                       payload: Optional[list | tuple] = None, 
                       data_object: Optional[IDataObject] = None) -> Command | None:
        if cmd_name in self.commands:
            return self.commands[cmd_name](context=context, message_service=message_service,
                                           event_bus=event_bus, screen=screen, payload=payload,
                                           data_object=data_object)
        return None


class CommandFactory:
    registry = CommandRegistry()

    @staticmethod
    def create_command(raw_command: str | tuple = None,
                       context: IServerContext | IClientContext | IControllerContext | IFileTransferService = None,
                       message_service: IMessageService = None, event_bus: IEventBus = None,
                       screen: Optional[str] = None, data_object: Optional[IDataObject] = None) -> Command | None:
        """
        Create command from either legacy raw_command or data_object.
        
        Args:
            raw_command: Legacy string or tuple command (optional)
            context: Command execution context
            message_service: Message service instance
            event_bus: Event bus instance
            screen: Screen identifier
            data_object: Structured data object (new approach)
        """
        
        # Prefer data_object approach over legacy
        if data_object is not None:
            cmd_name = data_object.data_type
            
            # Handle special mapping for file commands
            if cmd_name == "file" and hasattr(data_object, 'command'):
                cmd_name = data_object.command  # Use specific file command type (file_start, file_chunk, etc.)
            
            return CommandFactory.registry.create_command(
                cmd_name=cmd_name, context=context, message_service=message_service,
                event_bus=event_bus, screen=screen, payload=None, data_object=data_object
            )
        
        # Legacy support for string/tuple commands
        if raw_command is not None:
            if isinstance(raw_command, tuple):
                parts = raw_command
            else:
                parts = extract_command_parts(raw_command)

            cmd_name = parts[0]
            payload = parts[1:]
            return CommandFactory.registry.create_command(
                cmd_name=cmd_name, context=context, message_service=message_service,
                event_bus=event_bus, screen=screen, payload=payload, data_object=None
            )
        
        return None
