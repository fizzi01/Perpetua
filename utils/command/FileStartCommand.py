import logging
from typing import Optional

from utils.Interfaces import IBaseCommand, IFileTransferContext
from utils.protocol.message import ProtocolMessage, MessageBuilder


class FileStartCommand(IBaseCommand):

    DESCRIPTION = "file_start"

    def __init__(self, file_name: str, file_size: int, **kwargs):
        super().__init__(**kwargs)
        self.file_name = file_name
        self.file_size = file_size

    @classmethod
    def create(cls, file_name: str, file_size: int, screen: Optional[str] = None, **kwargs):
        """Create a file start command."""
        return cls(file_name=file_name, file_size=file_size, screen=screen, **kwargs)

    def to_protocol_message(self, source: Optional[str] = None, target: Optional[str] = None):
        """Convert to ProtocolMessage for transmission."""
        builder = MessageBuilder()
        return builder.create_file_message(
            "file_start",
            {"file_name": self.file_name, "file_size": self.file_size},
            source=source,
            target=target
        )

    def to_legacy_string(self) -> str:
        """Convert to legacy format_command string."""
        return f"file_start {self.file_name} {self.file_size}"

    @classmethod
    def from_legacy_string(cls, command_str: str, **kwargs):
        """Parse from legacy format_command string."""
        parts = command_str.split()
        if len(parts) >= 3 and parts[0] == "file_start":
            return cls(
                file_name=parts[1],
                file_size=int(parts[2]),
                **kwargs
            )
        return None

    def execute(self):
        try:
            requester = self.screen
            file_name = self.file_name
            file_size = self.file_size

            if isinstance(self.context, IFileTransferContext):
                self.context.file_transfer_service.handle_file_start(requester, file_name, file_size)
                logging.debug(f"({self.DESCRIPTION}) File start received from {requester}."
                              f"\n File name: {file_name}")
        except Exception as e:
            logging.debug(f"({self.DESCRIPTION}) error: {e}")

