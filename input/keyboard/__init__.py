from utils import backend_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from input.keyboard._base import *
else:
    # Load platform-specific mouse module
    _key_module = backend_module(__name__)

    # Define all classes and functions to be imported from this module
    ServerKeyboardListener = _key_module.ServerKeyboardListener
    ClientKeyboardController = _key_module.ClientKeyboardController
    # ---
    del _key_module