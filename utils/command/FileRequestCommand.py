import logging

from utils.command.Command import Command
from utils.Interfaces import IFileTransferContext
from utils.data import FileData


class FileRequestCommand(Command):

    DESCRIPTION = "file_request"

    def execute(self):
        try:
            requester = self.screen
            
            # Use structured data object if available, fallback to legacy payload
            if self.has_data_object() and isinstance(self.data_object, FileData):
                file_path = self.data_object.file_path or self.data_object.file_name
            elif self.has_legacy_payload() and self.payload:
                # If the payload has only one element, it means that the file path is the first element
                if len(self.payload) == 1:
                    file_path = self.payload[0]
                else:
                    file_path = self.payload[1]
            else:
                logging.error(f"({self.DESCRIPTION}) No valid data source available")
                return

            if isinstance(self.context, IFileTransferContext):  # Both server and client contexts implement this interface
                self.context.file_transfer_service.handle_file_request(requester)
                logging.debug(f"({self.DESCRIPTION}) File request received from {requester} "
                              f"-> {file_path}")

        except Exception as e:
            logging.debug(f"({self.DESCRIPTION}) error: {e}")


