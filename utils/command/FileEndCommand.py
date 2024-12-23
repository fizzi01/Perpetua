import logging

from utils.command.Command import Command
from utils.Interfaces import IFileTransferContext


class FileEndCommand(Command):

    DESCRIPTION = "file_end"

    def execute(self):
        try:
            owner = self.screen
            if isinstance(self.context, IFileTransferContext):
                self.context.file_transfer_service.handle_file_end(from_screen=owner)
                logging.debug(f"({self.DESCRIPTION}) File end from {owner}.")
        except Exception as e:
            logging.debug(f"({self.DESCRIPTION}) error: {e}")
