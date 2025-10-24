import logging

from utils.command.Command import Command
from utils.Interfaces import IFileTransferContext


class FileStartCommand(Command):

    DESCRIPTION = "file_start"

    def execute(self):
        try:
            requester = self.screen
            file_name = self.payload[0]
            file_size = int(self.payload[1])

            if isinstance(self.context, IFileTransferContext):
                self.context.file_transfer_service.handle_file_start(requester, file_name, file_size)
                logging.debug(f"({self.DESCRIPTION}) File start received from {requester}."
                              f"\n File name: {file_name}")
        except Exception as e:
            logging.debug(f"({self.DESCRIPTION}) error: {e}")

