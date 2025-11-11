from utils import backend_module

# Load platform-specific mouse module
_backend_module = backend_module(__name__)

Screen = _backend_module.Screen
del _backend_module

