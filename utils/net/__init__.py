from utils import backend_module

_backend_module = backend_module(__name__)
get_local_ip = _backend_module.get_local_ip
del _backend_module
