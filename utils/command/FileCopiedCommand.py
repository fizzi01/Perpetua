import logging
from typing import Optional

from utils.Interfaces import IBaseCommand, IFileTransferContext, IServerContext
from utils.protocol.message import ProtocolMessage, MessageBuilder


class FileCopiedCommand(IBaseCommand):

    DESCRIPTION = "file_copied"

    def __init__(self, file_name: str, file_size: int, file_path: str, **kwargs):
        super().__init__(**kwargs)
        self.file_name = file_name
        self.file_size = file_size
        self.file_path = file_path

    @classmethod
    def create(cls, file_name: str, file_size: int, file_path: str, screen: Optional[str] = None, **kwargs):
        """Create a file copied command."""
        return cls(file_name=file_name, file_size=file_size, file_path=file_path, screen=screen, **kwargs)

    def to_protocol_message(self, source: Optional[str] = None, target: Optional[str] = None):
        """Convert to ProtocolMessage for transmission."""
        builder = MessageBuilder()
        return builder.create_file_message(
            "file_copied",
            {
                "file_name": self.file_name,
                "file_size": self.file_size,
                "file_path": self.file_path
            },
            source=source,
            target=target
        )

    def to_legacy_string(self) -> str:
        """Convert to legacy format_command string."""
        return f"file_copied {self.file_name} {self.file_size} {self.file_path}"

    @classmethod
    def from_legacy_string(cls, command_str: str, **kwargs):
        """Parse from legacy format_command string."""
        parts = command_str.split()
        if len(parts) >= 4 and parts[0] == "file_copied":
            return cls(
                file_name=parts[1],
                file_size=int(parts[2]),
                file_path=parts[3],
                **kwargs
            )
        return None

    def execute(self):
        try:
            owner = self.screen
            file_name = self.file_name
            file_size = self.file_size
            file_path = self.file_path

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
