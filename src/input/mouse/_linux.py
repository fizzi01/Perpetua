"""
Provides mouse input support for Linux systems.
"""


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

from . import _base


class ServerMouseListener(_base.ServerMouseListener):
    """
    It listens for mouse events on Linux systems.
    Its main purpose is to capture mouse movements and clicks. And handle some border cases like cursor reaching screen edges.
    """

    MOVEMENT_HISTORY_N_THRESHOLD = 4
    MOVEMENT_HISTORY_LEN = 5

    pass


class ServerMouseController(_base.ServerMouseController):
    """
    It controls mouse events on Linux systems.
    Its main purpose is to move the mouse cursor and perform clicks.
    """

    pass


class ClientMouseController(_base.ClientMouseController):
    """
    It controls mouse events on Linux systems.
    Its main purpose is to move the mouse cursor and perform clicks.
    """

    MOVEMENT_HISTORY_N_THRESHOLD = 4
    MOVEMENT_HISTORY_LEN = 5

    pass
