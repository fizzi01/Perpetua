from utils.command.Command import CommandFactory

# Server Specific Commands imports
from .ClipboardCommand import ClipboardCommand
from .ReturnCommand import ReturnCommand
from .DisconnectCommand import DisconnectCommand

# Common Commands imports
from utils.command.FileChunkCommand import FileChunkCommand
from utils.command.FileEndCommand import FileEndCommand
from utils.command.FileStartCommand import FileStartCommand
from utils.command.FileRequestCommand import FileRequestCommand
from utils.command.FileCopiedCommand import FileCopiedCommand


# Register all commands here
CommandFactory.registry.register(ClipboardCommand.DESCRIPTION,
                                 lambda context, message_service, event_bus, screen, payload: ClipboardCommand(
                                     context=context, message_service=message_service, event_bus=event_bus,
                                     screen=screen, payload=payload))

CommandFactory.registry.register(ReturnCommand.DESCRIPTION,
                                 lambda context, message_service, event_bus, screen, payload: ReturnCommand(
                                     context, message_service, event_bus, screen, payload))

CommandFactory.registry.register(DisconnectCommand.DESCRIPTION,
                                 lambda context, message_service, event_bus, screen, payload: DisconnectCommand(
                                     context, message_service, event_bus, screen, payload))

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
                                 lambda context, message_service, event_bus, screen, payload: FileRequestCommand(
                                     context, message_service, event_bus, screen, payload))

CommandFactory.registry.register(FileCopiedCommand.DESCRIPTION,
                                 lambda context, message_service, event_bus, screen, payload: FileCopiedCommand(
                                     context, message_service, event_bus, screen, payload))
