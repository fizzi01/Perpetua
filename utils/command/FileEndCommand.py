import logging
from typing import Optional

from utils.Interfaces import IBaseCommand, IFileTransferContext
from utils.protocol.message import ProtocolMessage, MessageBuilder


class FileEndCommand(IBaseCommand):

    DESCRIPTION = "file_end"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    @classmethod
    def create(cls, screen: Optional[str] = None, **kwargs):
        """Create a file end command."""
        return cls(screen=screen, **kwargs)

    def to_protocol_message(self, source: Optional[str] = None, target: Optional[str] = None):
        """Convert to ProtocolMessage for transmission."""
        builder = MessageBuilder()
        return builder.create_file_message(
            "file_end",
            {},
            source=source,
            target=target
        )

    def to_legacy_string(self) -> str:
        """Convert to legacy format_command string."""
        return "file_end"

    @classmethod
    def from_legacy_string(cls, command_str: str, **kwargs):
        """Parse from legacy format_command string."""
        if command_str.strip() == "file_end":
            return cls(**kwargs)
        return None

    def execute(self):
        try:
            owner = self.screen
            if isinstance(self.context, IFileTransferContext):
                self.context.file_transfer_service.handle_file_end(from_screen=owner)
                logging.debug(f"({self.DESCRIPTION}) File end from {owner}.")
        except Exception as e:
            logging.debug(f"({self.DESCRIPTION}) error: {e}")
