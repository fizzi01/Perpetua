from utils import backend_module

# Load platform-specific mouse module
_mouse_module = backend_module(__name__)

# Define all classes and functions to be exported

# ---

del _mouse_module

