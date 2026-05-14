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


class _Override:
    """
    Descriptor returned by @override.

    Validation runs once at class-creation time via __set_name__ (instead of on
    every method call), and then the descriptor swaps itself out for the raw
    method so the runtime cost of the decorator is zero.
    """

    __slots__ = ("_method",)

    def __init__(self, method):
        self._method = method

    def __set_name__(self, owner, name):
        for cls in owner.__mro__[1:]:
            if name in cls.__dict__:
                # Bind the raw callable on the owner: future lookups skip us.
                setattr(owner, name, self._method)
                return
        raise NotImplementedError(
            f"Method '{name}' on '{owner.__name__}' does not override any method in superclass."
        )


def override(method):
    """
    Decorator that asserts a method overrides one from a superclass.
    Validation happens once when the owning class is created.
    """
    return _Override(method)
