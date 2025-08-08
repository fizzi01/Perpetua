import logging

from utils.command.Command import Command
from utils.Interfaces import IFileTransferContext, IServerContext


class FileCopiedCommand(Command):

    DESCRIPTION = "file_copied"

    def execute(self):
        try:
            owner = self.screen
            file_name = self.payload[0]
            file_size = int(self.payload[1])
            file_path = self.payload[2]

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
