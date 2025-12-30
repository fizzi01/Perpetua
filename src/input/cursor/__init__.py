from utils import backend_module
from typing import TYPE_CHECKING

# Load platform-specific mouse module
if TYPE_CHECKING:
    pass
else:
    _backend = backend_module(__name__)
    CursorHandlerWorker = _backend.CursorHandlerWorker
    del _backend
