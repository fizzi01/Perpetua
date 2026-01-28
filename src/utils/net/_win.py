
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
    @staticmethod
    def get_local_ip():
        """
        Retrieves the local IP address of the current machine by simulating an
        outbound connection to a public IP without sending any actual data.
        This method is useful for determining the internal network address
        used by the machine.

        Returns:
            str or None: The local IP address if successfully determined,
            otherwise None.

        Raises:
            Exception: If the local IP address cannot be determined for any
            reason, None is returned.
        """
        try:
            import socket

            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))
                ip = s.getsockname()[0]
                return ip
        except Exception as e:
            raise MissingIpError(f"Could not determine local IP address ({e})") from e
