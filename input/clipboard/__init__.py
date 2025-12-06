from utils import backend_module

# Load platform-specific mouse module
_clip_module = backend_module(__name__)

# Define all classes and functions to be imported from this module
ClipboardListener = _clip_module.ClipboardListener
ClipboardController = _clip_module.ClipboardController
# ---
del _clip_module