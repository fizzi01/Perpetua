import logging

from utils.command.Command import CommandFactory

# Server Specific Commands imports
from server.command.ClipboardCommand import ClipboardCommand
from server.command.ReturnCommand import ReturnCommand
from server.command.DisconnectCommand import DisconnectCommand

# Common Commands imports
from utils.command.FileChunkCommand import FileChunkCommand
from utils.command.FileEndCommand import FileEndCommand
from utils.command.FileStartCommand import FileStartCommand
from utils.command.FileRequestCommand import FileRequestCommand
from utils.command.FileCopiedCommand import FileCopiedCommand


def register_commands():
    logging.basicConfig(level=logging.ERROR)
    try:
        # Register all commands here with support for both legacy payload and data_object
        CommandFactory.registry.register(ClipboardCommand.DESCRIPTION,
                                         lambda context, message_service, event_bus, screen, payload, data_object=None: ClipboardCommand(
                                             context=context, message_service=message_service, event_bus=event_bus,
                                             screen=screen, payload=payload, data_object=data_object))

        CommandFactory.registry.register(ReturnCommand.DESCRIPTION,
                                         lambda context, message_service, event_bus, screen, payload, data_object=None: ReturnCommand(
                                             context, message_service, event_bus, screen, payload, data_object=data_object))

        CommandFactory.registry.register(DisconnectCommand.DESCRIPTION,
                                         lambda context, message_service, event_bus, screen, payload, data_object=None: DisconnectCommand(
                                             context, message_service, event_bus, screen, payload, data_object=data_object))

        CommandFactory.registry.register(FileChunkCommand.DESCRIPTION,
                                         lambda context, message_service, event_bus, screen, payload, data_object=None: FileChunkCommand(
                                             context, message_service, event_bus, screen, payload, data_object=data_object))

        CommandFactory.registry.register(FileEndCommand.DESCRIPTION,
                                         lambda context, message_service, event_bus, screen, payload, data_object=None: FileEndCommand(
                                             context, message_service, event_bus, screen, payload, data_object=data_object))

        CommandFactory.registry.register(FileStartCommand.DESCRIPTION,
                                         lambda context, message_service, event_bus, screen, payload, data_object=None: FileStartCommand(
                                             context, message_service, event_bus, screen, payload, data_object=data_object))

        CommandFactory.registry.register(FileRequestCommand.DESCRIPTION,
                                         lambda context, message_service, event_bus, screen, payload, data_object=None: FileRequestCommand(
                                             context, message_service, event_bus, screen, payload, data_object=data_object))

        CommandFactory.registry.register(FileCopiedCommand.DESCRIPTION,
                                         lambda context, message_service, event_bus, screen, payload, data_object=None: FileCopiedCommand(
                                             context, message_service, event_bus, screen, payload, data_object=data_object))
        return True
    except Exception as e:
        logging.error(f"Error while registering commands: {e}")
        return False
