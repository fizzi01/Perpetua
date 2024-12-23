import logging

from utils.command.Command import Command
from utils.Interfaces import IFileTransferContext


class FileRequestCommand(Command):

    DESCRIPTION = "file_request"

    def execute(self):
        try:
            requester = self.screen

            # If the payload has only one element, it means that the file path is the first element
            if len(self.payload) == 1:
                file_path = self.payload[0]
            else:
                file_path = self.payload[1]

            if isinstance(self.context, IFileTransferContext):  # Both server and client contexts implement this interface
                self.context.file_transfer_service.handle_file_request(requester)
                logging.debug(f"({self.DESCRIPTION}) File request received from {requester} "
                              f"-> {file_path}")

        except Exception as e:
            logging.debug(f"({self.DESCRIPTION}) error: {e}")


