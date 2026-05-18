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

from dataclasses import dataclass


@dataclass(frozen=True)
class MonitorInfo:
    """Geometry + metadata for a single connected display.

    Coordinates are in the OS global display coordinate space (origin at
    the primary monitor's top-left on macOS / Windows; the X server root
    origin on Linux/X11).
    """

    monitor_id: int
    min_x: int
    min_y: int
    max_x: int
    max_y: int
    is_primary: bool = False
    name: str = ""
    scaling_factor: float = 1.0

    @property
    def width(self) -> int:
        return self.max_x - self.min_x

    @property
    def height(self) -> int:
        return self.max_y - self.min_y

    @property
    def bbox(self) -> tuple[int, int, int, int]:
        return self.min_x, self.min_y, self.max_x, self.max_y

    def contains(self, x: float, y: float) -> bool:
        """``True`` if ``(x, y)`` falls inside this monitor's bounds."""
        return self.min_x <= x < self.max_x and self.min_y <= y < self.max_y

    def to_dict(self) -> dict:
        """Plain-dict view safe for JSON / msgpack round-trip with the GUI."""
        return {
            "monitor_id": self.monitor_id,
            "min_x": self.min_x,
            "min_y": self.min_y,
            "max_x": self.max_x,
            "max_y": self.max_y,
            "is_primary": self.is_primary,
            "name": self.name,
            "scaling_factor": self.scaling_factor,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "MonitorInfo":
        return cls(
            monitor_id=int(data["monitor_id"]),
            min_x=int(data["min_x"]),
            min_y=int(data["min_y"]),
            max_x=int(data["max_x"]),
            max_y=int(data["max_y"]),
            is_primary=bool(data.get("is_primary", False)),
            name=str(data.get("name", "")),
            scaling_factor=float(data.get("scaling_factor", 1.0)),
        )
