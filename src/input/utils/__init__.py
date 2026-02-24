import inspect
from typing import Optional
from pynput.keyboard import Key, KeyCode
import enum
from collections import deque
from typing import Callable

from model.client import ScreenPosition


class ButtonMapping(enum.Enum):
    """The various buttons.

    The actual values for these items differ between platforms. Some
    platforms may have additional buttons, but these are guaranteed to be
    present everywhere and we remap them to these values.
    """

    #: An unknown button was pressed
    unknown = 0

    #: The left button
    left = 1

    #: The middle button
    middle = 2

    #: The right button
    right = 3


class ScreenEdge(enum.Enum):
    LEFT = 1
    RIGHT = 2
    TOP = 3
    BOTTOM = 4


class EdgeDetector:
    """
    A utility class for detecting when the mouse cursor reaches the edges of the screen.
    """

    @staticmethod
    def clamp_to_screen(
        x: float | int, y: float | int, screen_size: tuple
    ) -> tuple[float, float]:
        """
        Clamps the given (x, y) coordinates to be within the bounds of the screen.

        Args:
            x (float | int): The x coordinate to clamp.
            y (float | int): The y coordinate to clamp.
            screen_size (tuple): A tuple representing the screen size (width, height).
        Returns:
            tuple[float, float]: The clamped (x, y) coordinates.
        """
        clamped_x = max(0, min(x, screen_size[0] - 1))
        clamped_y = max(0, min(y, screen_size[1] - 1))
        return clamped_x, clamped_y

    @staticmethod
    def is_at_edge(
        movement_history: deque | list,
        x: float | int,
        y: float | int,
        screen_size: tuple,
        is_dragging: bool,
        direction_ratio: float = 0.85,
    ) -> Optional[ScreenEdge]:
        """
        Determines if the cursor is moving towards and has reached any edge of the screen.

        Args:
            movement_history (deque | list): A deque or list of recent (x, y) positions of the cursor.
            x (float | int): Current x position of the cursor.
            y (float | int): Current y position of the cursor.
            screen_size (tuple): A tuple representing the screen size (width, height).
            is_dragging (bool): Whether the user is currently dragging (holding a button).
        Returns:
            Optional[ScreenEdge]: The edge the cursor is at, or None if not at any
        """
        if is_dragging:
            return None

        size = len(movement_history)
        if size < 2:
            return None

        w, h = screen_size

        x_edge = None
        x_axis_sign = 0
        if x <= 0:
            x_edge = ScreenEdge.LEFT
            x_axis_sign = -1
        elif x >= w - 1:
            x_edge = ScreenEdge.RIGHT
            x_axis_sign = 1

        y_edge = None
        y_axis_sign = 0
        if y <= 0:
            y_edge = ScreenEdge.TOP
            y_axis_sign = -1
        elif y >= h - 1:
            y_edge = ScreenEdge.BOTTOM
            y_axis_sign = 1

        if x_edge is None and y_edge is None:
            return None

        # Direction check with jitter tolerance
        pairs = size - 1
        min_agreements = int(pairs * direction_ratio)
        hist = movement_history

        # Check x-axis edge first (LEFT/RIGHT)
        if x_edge is not None:
            agreements = 0
            for i in range(pairs):
                if (hist[i + 1][0] - hist[i][0]) * x_axis_sign > 0:
                    agreements += 1
            if agreements >= min_agreements:
                return x_edge

        # Check y-axis edge (TOP/BOTTOM)
        if y_edge is not None:
            agreements = 0
            for i in range(pairs):
                if (hist[i + 1][1] - hist[i][1]) * y_axis_sign > 0:
                    agreements += 1
            if agreements >= min_agreements:
                return y_edge

        return None

    def detect_edge(
        self,
        movement_history: deque | list,
        x: float | int,
        y: float | int,
        screen_size: tuple,
        is_dragging: bool,
        callbacks: dict[ScreenEdge, Callable],
    ):
        """
        Detects if the cursor is at the edge and invokes the appropriate callback.

        Args:
            movement_history (deque | list): A deque or list of recent (x, y) positions of the cursor.
            x (float | int): Current x position of the cursor.
            y (float | int): Current y position of the cursor.
            screen_size (tuple): A tuple representing the screen size (width, height).
            is_dragging (bool): Whether the user is currently dragging (holding a button).
        """
        edge = self.is_at_edge(movement_history, x, y, screen_size, is_dragging)
        if edge and edge in callbacks:
            callbacks[edge]()

    @staticmethod
    def get_crossing_coords(
        x: float | int,
        y: float | int,
        screen_size: tuple,
        edge: ScreenEdge,
        screen: str | None,
    ) -> tuple[float, float]:
        """
        Get the coordinates when crossing back from client to server.
        Coords will be the opposite of the real one (so opposite to the edge reached).

        Args:
            x (float | int): Current x position of the cursor.
            y (float | int): Current y position of the cursor.
            screen_size (tuple): A tuple representing the screen size (width, height).
            edge (ScreenEdge): The edge that was reached.
        Returns:
            tuple[float, float]: The normalized crossing coordinates.
        """
        if screen == "" or screen is None:
            return -1, -1

        # If we reach the bottom edge, we need to set y to 1 (top of the server screen)
        if edge == ScreenEdge.BOTTOM and screen == ScreenPosition.TOP:
            return x / screen_size[0], 0.0
        # If we reach the top edge, we need to set y to 0 (bottom of the server screen)
        elif edge == ScreenEdge.TOP and screen == ScreenPosition.BOTTOM:
            return x / screen_size[0], 1.0
        # If we reach the left edge, we need to set x to 1 (right of the server screen)
        elif edge == ScreenEdge.LEFT and screen == ScreenPosition.RIGHT:
            return 1.0, y / screen_size[1]
        # If we reach the right edge, we need to set x to 0 (left of the server screen)
        elif edge == ScreenEdge.RIGHT and screen == ScreenPosition.LEFT:
            return 0.0, y / screen_size[1]
        else:
            return -1, -1


class KeyUtilities:
    """
    This class provides utility functions for keyboard key conversions.
    Like mapping key names from different OS into a specific os.
    """

    @staticmethod
    def map_key(key: str) -> Key | KeyCode | None:
        """
        For pynpuy Key are all special keys, and KeyCode are all character keys.
        """
        # First check if key is a special key in pynput
        try:
            special = Key[key]
            return special
        except KeyError:
            pass

        # Check if it's a vk_ key
        if key.startswith("vk_"):
            try:
                vk_code = int(key[3:])
                return KeyCode.from_vk(vk_code)
            except ValueError:
                pass

        # Next check if it's a single character (KeyCode)
        try:
            return KeyCode.from_char(key)
        except Exception:
            pass

        # Otherwise return the original string (unmapped)
        return None

    @staticmethod
    def map_vk(vk_code: int) -> KeyCode:
        """
        Maps a virtual key code to a pynput KeyCode.
        """
        return KeyCode.from_vk(vk_code)

    @staticmethod
    def map_to_key(kc: KeyCode) -> Key | None:
        """
        Maps a pynput KeyCode to a Key if possible, otherwise returns None.
        """
        try:
            return Key(kc)
        except (KeyError, AttributeError, ValueError):
            return None

    @staticmethod
    def is_special(
        key: Key | KeyCode | None, filter_out: Optional[list[Key]] = None
    ) -> bool:
        """
        Check if the given key is a special key (pynput Key) or a character key (KeyCode).
        Args:
            key (Key | KeyCode | None): The key to check.
            filter_out (Optional[list[Key]]): List of keys to filter out from being considered special.
        Returns:
            bool: True if the key is a special key and not in filter_out, False otherwise
        """
        if filter_out and key in filter_out:
            return False

        return isinstance(key, Key)


def _wrap(f, args):
    """Wraps a callable to make it accept ``args`` number of arguments.

    :param f: The callable to wrap. If this is ``None`` a no-op wrapper is
        returned.

    :param int args: The number of arguments to accept.

    :raises ValueError: if f requires more than ``args`` arguments
    """
    if f is None:
        return lambda *a: None
    else:
        argspec = inspect.getfullargspec(f)
        actual = len(inspect.signature(f).parameters)
        defaults = len(argspec.defaults) if argspec.defaults else 0
        if actual - defaults > args:
            raise ValueError(f)
        elif actual >= args or argspec.varargs is not None:
            return f
        else:
            return lambda *a: f(*a[:actual])
