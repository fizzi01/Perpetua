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

from . import _base

_IS_WAYLAND = (
    "WAYLAND_DISPLAY" in os.environ or os.environ.get("XDG_SESSION_TYPE") == "wayland"
)


class Screen(_base.Screen):
    _size_cache: tuple[int, int] | None = None
    _wl_display = None  # Shared singleton kept alive for the process

    @classmethod
    def get_size(cls) -> tuple[int, int]:
        """
        Get the current screen size on Linux (X11 and Wayland).
        """
        if _IS_WAYLAND:
            return cls._get_size_wayland()
        return cls._get_size_x11()

    @classmethod
    def _get_size_x11(cls) -> tuple[int, int]:
        try:
            from Xlib import display as xdisplay

            d = xdisplay.Display()
            screen = d.screen()
            width = screen.width_in_pixels
            height = screen.height_in_pixels
            d.close()
            return width, height
        except Exception:
            print("Unable to get screen size. Display may not be available.")
            return 0, 0

    @classmethod
    def _get_size_wayland(cls) -> tuple[int, int]:
        # Primary: python-wayland — native Wayland protocol via wl_output
        if cls._size_cache:
            return cls._size_cache

        import wayland as _wayland
        from wayland.client import wayland_class as _wayland_class

        @_wayland_class("wl_registry")
        class _WlRegistry(_wayland.wl_registry):
            def __init__(self):
                super().__init__()
                self.outputs = []

            def on_global(self, name, interface, version):
                if interface == "wl_output":
                    self.outputs.append(self.bind(name, interface, version))  # ty:ignore[missing-argument]

        @_wayland_class("wl_output")
        class _WlOutput(_wayland.wl_output):
            def __init__(self):
                super().__init__()
                self.size: tuple[int, int] | None = None
                self.done = False

            def on_mode(self, flags, width, height, refresh):
                if flags & 1:  # current mode
                    self.size = (width, height)

            def on_done(self):
                self.done = True

        try:
            if cls._wl_display is None:
                cls._wl_display = _wayland.wl_display()
            display = cls._wl_display
            registry = display.get_registry()

            for _ in range(50):
                display.dispatch_timeout(0.1)
                if registry.outputs and all(o.done for o in registry.outputs):  # ty:ignore[unresolved-attribute]
                    break

            for output in registry.outputs:  # ty:ignore[unresolved-attribute]
                if output.size:
                    cls._size_cache = output.size
                    return output.size
        except Exception:
            pass

        print("Unable to get screen size on Wayland.")
        return 0, 0

    @classmethod
    def is_screen_locked(cls) -> bool:
        """
        Monitor display sleep/wake events on Linux.
        """
        return False  # Placeholder implementation

    @classmethod
    def hide_icon(cls):
        pass  # Placeholder implementation
