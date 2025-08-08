import logging

from utils.command.Command import Command
from utils.Interfaces import IFileTransferContext, IServerContext
from utils.data import FileData


class FileCopiedCommand(Command):

    DESCRIPTION = "file_copied"

    def execute(self):
        try:
            owner = self.screen
            
            # Use structured data object if available, fallback to legacy payload
            if self.has_data_object() and isinstance(self.data_object, FileData):
                file_name = self.data_object.file_name
                file_size = self.data_object.file_size
                file_path = self.data_object.file_path
            elif self.has_legacy_payload() and self.payload and len(self.payload) >= 3:
                file_name = self.payload[0]
                file_size = int(self.payload[1])
                file_path = self.payload[2]
            else:
                logging.error(f"({self.DESCRIPTION}) No valid data source available")
                return

            if (isinstance(self.context, IServerContext)
                    and isinstance(self.context, IFileTransferContext)):

                self.context.file_transfer_service.handle_file_copy(
                    caller_screen=owner,
                    file_name=file_name,
                    file_path=file_path,
                    file_size=file_size
                )

                logging.debug(f"[CMD] ({self.DESCRIPTION}) Received File info from {owner}."
                              f"\n File name: {file_name} "
                              f"\n File size: {file_size} bytes")

            elif isinstance(self.context, IFileTransferContext):    # Im a client
                self.context.file_transfer_service.handle_file_copy_external(
                    file_name=file_name,
                    file_size=file_size,
                    file_path=file_path
                )

                logging.debug(f"({self.DESCRIPTION}) Received File info from Server."
                              f"\n File name: {file_name} "
                              f"\n File size: {file_size} bytes")

        except Exception as e:
            logging.debug(f"({self.DESCRIPTION}) error: {e}")
