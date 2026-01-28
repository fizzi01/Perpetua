
#  Perpatua - open-source and cross-platform KVM software.
#  Copyright (c) 2026 Federico Izzi.
#
#  This program is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program.  If not, see <https://www.gnu.org/licenses/>.
#

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
