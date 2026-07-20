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

"""Public surface: ``AutostartManager`` resolves to the per-OS backend.

Usage::

    from utils.autostart import AutostartManager
    mgr = AutostartManager()
    if mgr.is_enabled().enabled: ...
    mgr.enable("/path/to/Perpetua")
    mgr.disable()
"""

from typing import TYPE_CHECKING

from utils import backend_module

from ._base import (
    MODE_CLIENT,
    MODE_OFF,
    MODE_PLAIN,
    MODE_SERVER,
    AutostartStatus,
    args_for_mode,
    mode_from_args,
)

if TYPE_CHECKING:
    from ._base import AutostartManager
else:
    _backend_module = backend_module(__name__)
    AutostartManager = _backend_module.AutostartManager
    del _backend_module

__all__ = [
    "AutostartManager",
    "AutostartStatus",
    "args_for_mode",
    "mode_from_args",
    "MODE_OFF",
    "MODE_SERVER",
    "MODE_CLIENT",
    "MODE_PLAIN",
]
