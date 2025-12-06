import sys
import importlib


def backend_module(package: str, platform_map: dict = None):
    """
    Dynamically loads the platform-specific module for the operating system.

    Args:
        package: Package name (e.g. 'input.mouse')
        platform_map: Dictionary mapping sys.platform to module names.
                     If None, uses the _<platform> convention

    Returns:
        The loaded module

    Raises:
        OSError: If the operating system is not supported
        ImportError: If the module cannot be imported
    """
    if platform_map is None:
        platform_map = {
            'darwin': '_darwin',
            'win32': '_win',
            'linux': '_linux',
        }

    platform = sys.platform
    module_name = platform_map.get(platform)

    if module_name is None:
        raise OSError(f"Unsupported operating system: {platform}")

    try:
        module = importlib.import_module(f'.{module_name}', package=package)
        return module
    except ImportError as e:
        # Fallback on _base if specific module not found
        try:
            module = importlib.import_module(f'._base', package=package)
            return module
        except ImportError:
            raise ImportError(f"Unable to load module for {platform}: {e}")


def export_module_symbols(module, target_globals):
    """
    Exports all public symbols from a module to the target namespace.

    Args:
        module: The module to export from
        target_globals: The globals() dictionary of the target module
    """
    for attr in dir(module):
        if not attr.startswith('_'):
            target_globals[attr] = getattr(module, attr)
