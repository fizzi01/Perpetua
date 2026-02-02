#  Perpetua - open-source and cross-platform KVM software.
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


class MissingIpError(Exception):
    """Custom exception raised when the local IP address cannot be determined."""

    pass


class CommonNetInfo:
    """Common network information class for shared attributes or methods."""

    @staticmethod
    def get_local_ip():
        """
        Placeholder function for retrieving the local IP address.
        This function should be implemented in platform-specific modules.
        """
        raise NotImplementedError(
            "get_local_ip() must be implemented in platform-specific modules."
        )
