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

from wx import App, Display

from . import _base


class Screen(_base.Screen):
    @classmethod
    def get_size(cls) -> tuple[int, int]:
        app = App(False)
        display = Display()
        del app
        geometry = display.GetGeometry()
        return geometry.width, geometry.height

    @classmethod
    def is_screen_locked(cls) -> bool:
        """
        Monitor display sleep/wake events on Linux.
        """
        return False  # Placeholder implementation

    @classmethod
    def hide_icon(cls):
        pass  # Placeholder implementation