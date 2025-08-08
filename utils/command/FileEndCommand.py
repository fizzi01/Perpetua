import logging

from utils.command.Command import Command
from utils.Interfaces import IFileTransferContext
from utils.data import FileData


class FileEndCommand(Command):

    DESCRIPTION = "file_end"

    def execute(self):
        try:
            owner = self.screen
            
            # Use structured data object if available (FileData for file_end might not have additional data)
            # This command doesn't typically need payload data, so we just need to check if it's a file_end
            if self.has_data_object() and isinstance(self.data_object, FileData):
                # Validate it's the correct command type
                if not self.data_object.is_file_end():
                    logging.error(f"({self.DESCRIPTION}) Expected file_end command, got {self.data_object.command}")
                    return
            elif self.has_legacy_payload():
                # Legacy doesn't need payload validation for file_end
                pass
            # No else clause needed - file_end doesn't require specific data
            
            if isinstance(self.context, IFileTransferContext):
                self.context.file_transfer_service.handle_file_end(from_screen=owner)
                logging.debug(f"({self.DESCRIPTION}) File end from {owner}.")
        except Exception as e:
            logging.debug(f"({self.DESCRIPTION}) error: {e}")
