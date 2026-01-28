
#  Perpatua - open-source and cross-platform KVM software.
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

from utils import backend_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._base import ClipboardListener, ClipboardController, ClipboardType, Clipboard
else:
    # Load platform-specific mouse module
    _clip_module = backend_module(__name__)

    # Define all classes and functions to be imported from this module
    ClipboardListener = _clip_module.ClipboardListener
    ClipboardController = _clip_module.ClipboardController
    ClipboardType = _clip_module.ClipboardType
    Clipboard = _clip_module.Clipboard
    # ---
    del _clip_module
