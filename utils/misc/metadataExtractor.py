import inspect


def format_method_name(name: str) -> str:
    """
    Converte un nome di metodo in una stringa leggibile.
    Es: 'get_server_ip' -> 'Server IP'
    """
    name = name.replace("get_", "").replace("set_", "")  # Rimuove il prefisso
    name = name.replace("_", " ")  # Sostituisce gli underscore con spazi
    return name.title()  # Trasforma in Title Case


def extract_metadata(cls):
    """
    Extract metadata from a class
    :param cls:  to extract metadata from
    :return: metadata extracted from the class
    """
    mapped_methods = {"get_methods": {}, "set_methods": {}}

    # Itera sui membri definiti nella classe stessa
    for name, obj in inspect.getmembers(cls, predicate=inspect.isfunction):
        if name.startswith("get_"):
            signature = inspect.signature(obj)
            mapped_methods["get_methods"][name] = {
                "display_name": format_method_name(name),
                "return_type": str(
                    signature.return_annotation) if signature.return_annotation != inspect.Signature.empty else None
            }
        elif name.startswith("set_"):
            signature = inspect.signature(obj)
            params = [
                {
                    "name": param_name,
                    "type": str(param.annotation) if param.annotation != inspect.Parameter.empty else None,
                    "default": param.default if param.default != inspect.Parameter.empty else None,
                }
                for param_name, param in signature.parameters.items()
                if param_name != "self"
            ]
            mapped_methods["set_methods"][name] = {
                "display_name": format_method_name(name),
                "parameters": params
            }

    return mapped_methods
