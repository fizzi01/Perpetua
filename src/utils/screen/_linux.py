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

import re
import subprocess

from Xlib import display as xdisplay

from . import _base


class Screen(_base.Screen):
    @classmethod
    def get_size(cls) -> tuple[int, int]:
        """
        Returns the size of the primary screen as a tuple (width, height).

        Strategy (fastest first):
        1. python-xlib  — direct in-process X11 socket call, zero subprocess overhead.
                          Works on X11 and Xwayland (most Wayland compositors).
        2. xrandr       — subprocess fallback for native Wayland sessions without Xwayland.
        """
        try:
            d = xdisplay.Display()
            s = d.screen()
            width, height = s.width_in_pixels, s.height_in_pixels
            d.close()
            return width, height
        except Exception:
            return cls._get_size_xrandr()

    @classmethod
    def _get_size_xrandr(cls) -> tuple[int, int]:
        """
        Fallback: parse the 'current WxH' token from xrandr --current output.
        Spawns a subprocess but avoids requiring any additional Python dependency.
        """
        try:
            out = subprocess.check_output(
                ["xrandr", "--current"], text=True, timeout=2, stderr=subprocess.DEVNULL
            )
            m = re.search(r"current\s+(\d+)\s*x\s*(\d+)", out)
            if m:
                return int(m.group(1)), int(m.group(2))
        except Exception:
            pass
        raise RuntimeError("Unable to determine screen size on Linux")
