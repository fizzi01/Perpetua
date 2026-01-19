from utils import backend_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._base import PermissionChecker
else:
    _backend_module = backend_module(__name__)
    PermissionChecker = _backend_module.PermissionChecker
    del _backend_module