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


class Screen:
    @classmethod
    def get_size(cls) -> tuple[int, int]:
        """
        Returns the size of the primary screen as a tuple (width, height).
        """
        raise NotImplementedError("Screen size retrieval not implemented for this OS.")

    @classmethod
    def get_size_str(cls) -> str:
        """
        Returns the size of the primary screen as a string "widthxheight".
        """
        width, height = cls.get_size()
        return f"{width:.0f}x{height:.0f}"

    @classmethod
    def is_screen_locked(cls) -> bool:
        """
        Checks if the screen is currently locked.
        """
        raise NotImplementedError(
            "Screen lock status check not implemented for this OS."
        )

    @classmethod
    def hide_icon(cls):
        """
        Hides the application icon from the taskbar/dock.
        """
        raise NotImplementedError("Icon hiding not implemented for this OS.")
