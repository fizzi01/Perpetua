import logging

from utils.command.Command import Command
from utils.Interfaces import IFileTransferContext
from utils.data import FileData


class FileStartCommand(Command):

    DESCRIPTION = "file_start"

    def execute(self):
        try:
            requester = self.screen
            
            # Use structured data object if available, fallback to legacy payload
            if self.has_data_object() and isinstance(self.data_object, FileData):
                file_name = self.data_object.file_name
                file_size = self.data_object.file_size
            elif self.has_legacy_payload() and self.payload and len(self.payload) >= 2:
                file_name = self.payload[0]
                file_size = int(self.payload[1])
            else:
                logging.error(f"({self.DESCRIPTION}) No valid data source available")
                return

            if isinstance(self.context, IFileTransferContext):
                self.context.file_transfer_service.handle_file_start(requester, file_name, file_size)
                logging.debug(f"({self.DESCRIPTION}) File start received from {requester}."
                              f"\n File name: {file_name}")
        except Exception as e:
            logging.debug(f"({self.DESCRIPTION}) error: {e}")

