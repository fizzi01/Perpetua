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

import os
from Xlib import display

from . import _base

# Check for wayland not supported
if "WAYLAND_DISPLAY" in os.environ or "XDG_SESSION_TYPE" in os.environ and os.environ["XDG_SESSION_TYPE"] == "wayland":
    raise NotImplementedError("Wayland is not supported yet.")

class Screen(_base.Screen):
    @classmethod
    def get_size(cls) -> tuple[int, int]:
        """
        Get the current screen size on Linux.
        """
        try:
            d = display.Display()
            screen = d.screen()
            width = screen.width_in_pixels
            height = screen.height_in_pixels
            d.close()
        except Exception:
            print("Unable to get screen size. Display may not be available.")
            return 0, 0  # Return default size if display is not available
        return width, height

    @classmethod
    def is_screen_locked(cls) -> bool:
        """
        Monitor display sleep/wake events on Linux.
        """
        return False  # Placeholder implementation

    @classmethod
    def hide_icon(cls):
        pass  # Placeholder implementation
