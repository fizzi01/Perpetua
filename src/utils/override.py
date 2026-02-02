"""
Override decorator to replace methods in classes.
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


def override(method):
    """
    A decorator to indicate that a method is intended to override a method in a superclass.
    Raises an error if the method does not actually override any method in the superclass.
    """

    def wrapper(self, *args, **kwargs):
        # Check if the method exists in any superclass
        for cls in self.__class__.__mro__[1:]:
            if method.__name__ in cls.__dict__:
                return method(self, *args, **kwargs)
        raise NotImplementedError(
            f"Method '{method.__name__}' does not override any method in superclass."
        )

    return wrapper
