
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

import netifaces

from . import _base
from ._base import MissingIpError


class CommonNetInfo(_base.CommonNetInfo):
    @staticmethod
    def get_local_ip():
        """
        Determines the local IP address of the "en0" network interface if it exists and has an associated
        IPv4 address.

        This function attempts to retrieve the IPv4 address of the "en0" network interface using the
        `netifaces` library. If the "en0" interface is not present or does not have an IPv4 address, the
        function will log a warning message and return `None`. If any exception occurs during the retrieval
        process, an error message is printed, and the function returns `None`.

        Returns:
            str | None: The IPv4 address of the "en0" interface if it exists, otherwise `None`.

        Raises:
            Exception: If the local IP address cannot be determined for any reason None is returned.
        """
        try:
            interfaces = netifaces.interfaces()
            if "en0" in interfaces:
                addresses = netifaces.ifaddresses("en0")
                if netifaces.AF_INET in addresses:
                    ip = addresses[netifaces.AF_INET][0]["addr"]
                    return ip
            raise MissingIpError("en0 interface not found or has no IPv4 address")
        except Exception as e:
            raise MissingIpError(f"Could not determine local IP address ({e})") from e
