import logging

from utils.command.Command import Command
from utils.Interfaces import IFileTransferContext


class FileChunkCommand(Command):

    DESCRIPTION = "file_chunk"

    def execute(self):
        try:
            owner = self.screen
            chunk_data = self.payload[0]
            chunk_index = int(self.payload[1])

            if isinstance(self.context, IFileTransferContext):
                self.context.file_transfer_service.handle_file_chunk(from_screen=owner,
                                                                     encoded_chunk=chunk_data,
                                                                     chunk_index=chunk_index)
                logging.debug(f"({FileChunkCommand}) File chunk received from {owner}. "
                              f"\n Chunk index: {chunk_index}")
        except Exception as e:
            logging.debug(f"({FileChunkCommand}) error: {e}")
