from utils import backend_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._base import Screen
else:
    # Load platform-specific mouse module
    _backend_module = backend_module(__name__)

    Screen = _backend_module.Screen
    del _backend_module
