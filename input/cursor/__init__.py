from utils import backend_module

# Load platform-specific mouse module
_backend = backend_module(__name__)
CursorHandlerWorker = _backend.CursorHandlerWorker
del _backend