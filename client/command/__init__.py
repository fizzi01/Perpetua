import logging

from utils.command.Command import CommandFactory

from utils.command.FileChunkCommand import FileChunkCommand
from utils.command.FileEndCommand import FileEndCommand
from utils.command.FileStartCommand import FileStartCommand
from utils.command.FileRequestCommand import FileRequestCommand
from utils.command.FileCopiedCommand import FileCopiedCommand

from client.command.MouseCommand import MouseCommand
from client.command.KeyboardCommand import KeyboardCommand
from client.command.ClipboardCommand import ClipboardCommand


def register_commands():
    logging.basicConfig(level=logging.ERROR)
    try:
        # Register all commands here with support for both legacy payload and data_object
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

        CommandFactory.registry.register(MouseCommand.DESCRIPTION,
                                         lambda context, message_service, event_bus, screen, payload, data_object=None: MouseCommand(
                                             context, message_service, event_bus, screen, payload, data_object=data_object))

        CommandFactory.registry.register(KeyboardCommand.DESCRIPTION,
                                         lambda context, message_service, event_bus, screen, payload, data_object=None: KeyboardCommand(
                                             context, message_service, event_bus, screen, payload, data_object=data_object))

        CommandFactory.registry.register(ClipboardCommand.DESCRIPTION,
                                         lambda context, message_service, event_bus, screen, payload, data_object=None: ClipboardCommand(
                                             context, message_service, event_bus, screen, payload, data_object=data_object))
        return True
    except Exception as e:
        logging.error(f"Error while registering commands: {e}")
        return False
