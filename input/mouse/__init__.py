from utils import backend_module

# Load platform-specific mouse module
_mouse_module = backend_module(__name__)

# Define all classes and functions to be imported from this module
ServerMouseListener = _mouse_module.ServerMouseListener
ServerMouseController = _mouse_module.ServerMouseController
ClientMouseController = _mouse_module.ClientMouseController
# ---
del _mouse_module

