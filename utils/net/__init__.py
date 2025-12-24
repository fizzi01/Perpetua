from utils import backend_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._base import CommonNetInfo
    get_local_ip = CommonNetInfo.get_local_ip
else:
    _backend_module = backend_module(__name__)
    CommonNetInfo = _backend_module.CommonNetInfo
    get_local_ip = CommonNetInfo.get_local_ip
    del _backend_module
