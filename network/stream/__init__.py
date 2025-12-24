from enum import IntEnum


class StreamType(IntEnum):
    """
    Enumeration of different stream types with priority levels.
    """

    COMMAND = 0  # High priority - bidirectional commands
    KEYBOARD = 4  # High priority - keyboard events
    MOUSE = 1  # High priority - mouse movements (high frequency)
    CLIPBOARD = 12  # Low priority - clipboard
    FILE = 16  # Low priority - file transfers

    @classmethod
    def is_valid(cls, stream_type: int) -> bool:
        """
        Verify if the given stream type is valid.
        """
        try:
            cls(stream_type)
            return True
        except ValueError:
            return False
