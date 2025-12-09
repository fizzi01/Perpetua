from utils import backend_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from input.clipboard._base import *
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