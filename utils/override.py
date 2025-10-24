"""
Override decorator to replace methods in classes.
"""

def override(method):
    """
    A decorator to indicate that a method is intended to override a method in a superclass.
    Raises an error if the method does not actually override any method in the superclass.
    """
    def wrapper(self, *args, **kwargs):
        # Check if the method exists in any superclass
        for cls in self.__class__.__mro__[1:]:
            if method.__name__ in cls.__dict__:
                return method(self, *args, **kwargs)
        raise NotImplementedError(f"Method '{method.__name__}' does not override any method in superclass.")
    return wrapper