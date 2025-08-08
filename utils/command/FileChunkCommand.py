import logging
from typing import Optional

from utils.Interfaces import IBaseCommand, IFileTransferContext
from utils.protocol.message import ProtocolMessage, MessageBuilder


class FileChunkCommand(IBaseCommand):

    DESCRIPTION = "file_chunk"

    def __init__(self, chunk_data: str, chunk_index: int, **kwargs):
        super().__init__(**kwargs)
        self.chunk_data = chunk_data
        self.chunk_index = chunk_index

    @classmethod
    def create(cls, chunk_data: str, chunk_index: int, screen: Optional[str] = None, **kwargs):
        """Create a file chunk command."""
        return cls(chunk_data=chunk_data, chunk_index=chunk_index, screen=screen, **kwargs)

    def to_protocol_message(self, source: Optional[str] = None, target: Optional[str] = None):
        """Convert to ProtocolMessage for transmission."""
        builder = MessageBuilder()
        return builder.create_file_message(
            "file_chunk",
            {"chunk_data": self.chunk_data, "chunk_index": self.chunk_index},
            source=source,
            target=target
        )

    def to_legacy_string(self) -> str:
        """Convert to legacy format_command string."""
        return f"file_chunk {self.chunk_data} {self.chunk_index}"

    @classmethod
    def from_legacy_string(cls, command_str: str, **kwargs):
        """Parse from legacy format_command string."""
        parts = command_str.split()
        if len(parts) >= 3 and parts[0] == "file_chunk":
            return cls(
                chunk_data=parts[1],
                chunk_index=int(parts[2]),
                **kwargs
            )
        return None

    def execute(self):
        try:
            owner = self.screen
            chunk_data = self.chunk_data
            chunk_index = self.chunk_index

            if isinstance(self.context, IFileTransferContext):
                self.context.file_transfer_service.handle_file_chunk(from_screen=owner,
                                                                     encoded_chunk=chunk_data,
                                                                     chunk_index=chunk_index)
                logging.debug(f"({self.__class__.__name__}) File chunk received from {owner}. "
                              f"\n Chunk index: {chunk_index}")
        except Exception as e:
            logging.debug(f"({self.__class__.__name__}) error: {e}")
