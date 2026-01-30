
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

from . import _base
from ._base import MissingIpError


class CommonNetInfo(_base.CommonNetInfo):
    HOST: str = "8.8.8.8"
    PORT: int = 53
    TIMEOUT: int = 3
    @staticmethod
    def get_local_ip():
        """
        Retrieves the local IP address of the current machine by simulating an
        outbound connection to a public IP without sending any actual data.

        :raises MissingIpError: If the local IP address cannot be determined due to an exception during
            the operation, the error details are encapsulated in the exception's message and raised.
        :return: The local IP address of the machine as a string.
        :rtype: str
        """
        try:
            import socket

            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(CommonNetInfo.TIMEOUT)
                s.connect((CommonNetInfo.HOST, CommonNetInfo.PORT))
                ip = s.getsockname()[0]
                return ip
        except Exception as e:
            raise MissingIpError(f"Could not determine local IP address ({e})") from e
