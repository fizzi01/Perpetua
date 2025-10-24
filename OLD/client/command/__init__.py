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
        # Register all commands here
        CommandFactory.registry.register(FileChunkCommand.DESCRIPTION,
                                         lambda context, message_service, event_bus, screen, payload: FileChunkCommand(
                                             context, message_service, event_bus, screen, payload))

        CommandFactory.registry.register(FileEndCommand.DESCRIPTION,
                                         lambda context, message_service, event_bus, screen, payload: FileEndCommand(
                                             context, message_service, event_bus, screen, payload))

        CommandFactory.registry.register(FileStartCommand.DESCRIPTION,
                                         lambda context, message_service, event_bus, screen, payload: FileStartCommand(
                                             context, message_service, event_bus, screen, payload))

        CommandFactory.registry.register(FileRequestCommand.DESCRIPTION,
                                         lambda context, message_service, event_bus, screen,
                                                payload: FileRequestCommand(
                                             context, message_service, event_bus, screen, payload))

        CommandFactory.registry.register(FileCopiedCommand.DESCRIPTION,
                                         lambda context, message_service, event_bus, screen, payload: FileCopiedCommand(
                                             context, message_service, event_bus, screen, payload))

        CommandFactory.registry.register(MouseCommand.DESCRIPTION,
                                         lambda context, message_service, event_bus, screen, payload: MouseCommand(
                                             context, message_service, event_bus, screen, payload))

        CommandFactory.registry.register(KeyboardCommand.DESCRIPTION,
                                         lambda context, message_service, event_bus, screen, payload: KeyboardCommand(
                                             context, message_service, event_bus, screen, payload))

        CommandFactory.registry.register(ClipboardCommand.DESCRIPTION,
                                         lambda context, message_service, event_bus, screen, payload: ClipboardCommand(
                                             context, message_service, event_bus, screen, payload))
        return True
    except Exception as e:
        logging.error(f"Error while registering commands: {e}")
        return False
