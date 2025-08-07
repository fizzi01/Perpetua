import logging
from typing import Optional

from utils.Interfaces import IBaseCommand, IFileTransferContext
from utils.protocol.message import ProtocolMessage, MessageBuilder


class FileRequestCommand(IBaseCommand):

    DESCRIPTION = "file_request"

    def __init__(self, file_path: Optional[str] = None, **kwargs):
        super().__init__(**kwargs)
        self.file_path = file_path

    @classmethod
    def create(cls, file_path: Optional[str] = None, screen: Optional[str] = None, **kwargs):
        """Create a file request command."""
        return cls(file_path=file_path, screen=screen, **kwargs)

    def to_protocol_message(self, source: Optional[str] = None, target: Optional[str] = None):
        """Convert to ProtocolMessage for transmission."""
        builder = MessageBuilder()
        data = {}
        if self.file_path:
            data["file_path"] = self.file_path
        return builder.create_file_message(
            "file_request",
            data,
            source=source,
            target=target
        )

    def to_legacy_string(self) -> str:
        """Convert to legacy format_command string."""
        if self.file_path:
            return f"file_request {self.file_path}"
        return "file_request"

    @classmethod
    def from_legacy_string(cls, command_str: str, **kwargs):
        """Parse from legacy format_command string."""
        parts = command_str.split()
        if len(parts) >= 1 and parts[0] == "file_request":
            file_path = parts[1] if len(parts) > 1 else None
            return cls(file_path=file_path, **kwargs)
        return None

    def execute(self):
        try:
            requester = self.screen

            if isinstance(self.context, IFileTransferContext):  # Both server and client contexts implement this interface
                self.context.file_transfer_service.handle_file_request(requester)
                logging.debug(f"({self.DESCRIPTION}) File request received from {requester} "
                              f"-> {self.file_path or 'default path'}")

        except Exception as e:
            logging.debug(f"({self.DESCRIPTION}) error: {e}")
