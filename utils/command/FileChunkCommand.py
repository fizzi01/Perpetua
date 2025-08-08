import logging

from utils.command.Command import Command
from utils.Interfaces import IFileTransferContext
from utils.data import FileData


class FileChunkCommand(Command):

    DESCRIPTION = "file_chunk"

    def execute(self):
        try:
            owner = self.screen
            
            # Use structured data object if available, fallback to legacy payload
            if self.has_data_object() and isinstance(self.data_object, FileData):
                chunk_data = self.data_object.chunk_data
                chunk_index = self.data_object.chunk_index
            elif self.has_legacy_payload() and self.payload and len(self.payload) >= 2:
                chunk_data = self.payload[0]
                chunk_index = int(self.payload[1])
            else:
                logging.error(f"({self.DESCRIPTION}) No valid data source available")
                return

            if isinstance(self.context, IFileTransferContext):
                self.context.file_transfer_service.handle_file_chunk(from_screen=owner,
                                                                     encoded_chunk=chunk_data,
                                                                     chunk_index=chunk_index)
                logging.debug(f"({FileChunkCommand}) File chunk received from {owner}. "
                              f"\n Chunk index: {chunk_index}")
        except Exception as e:
            logging.debug(f"({FileChunkCommand}) error: {e}")
