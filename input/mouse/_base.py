import enum
from typing import Callable, Optional

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
    def is_at_edge(movement_history: list, x: float | int, y: float | int, screen_size: tuple) -> Optional[ScreenEdge]:
        """
        Determines if the cursor is moving towards and has reached any edge of the screen.

        Args:
            movement_history (list): A list of recent (x, y) positions of the cursor.
            x (float | int): Current x position of the cursor.
            y (float | int): Current y position of the cursor.
            screen_size (tuple): A tuple representing the screen size (width, height).
        Returns:
            Optional[ScreenEdge]: The edge the cursor is at, or None if not at any
        """
        # Check all the previous movements to determine the direction
        queue_data = movement_history
        queue_size = len(queue_data)

        moving_towards_left = all(queue_data[i][0] > queue_data[i + 1][0] for i in range(queue_size - 1))
        moving_towards_right = all(queue_data[i][0] < queue_data[i + 1][0] for i in range(queue_size - 1))
        moving_towards_top = all(queue_data[i][1] > queue_data[i + 1][1] for i in range(queue_size - 1))
        moving_towards_bottom = all(queue_data[i][1] < queue_data[i + 1][1] for i in range(queue_size - 1))

        # Check if we are at the edges
        at_left_edge = x <= 0
        at_right_edge = x >= screen_size[0] - 1
        at_top_edge = y <= 0
        at_bottom_edge = y >= screen_size[1] - 1

        edge = None

        if at_left_edge and moving_towards_left:
            edge = ScreenEdge.LEFT
        elif at_right_edge and moving_towards_right:
            edge = ScreenEdge.RIGHT
        elif at_top_edge and moving_towards_top:
            edge = ScreenEdge.TOP
        elif at_bottom_edge and moving_towards_bottom:
            edge = ScreenEdge.BOTTOM

        return edge


    def detect_edge(self, movement_history: list, x: float | int, y: float | int, screen_size: tuple, callbacks: dict[ScreenEdge,Callable]):
        """
        Detects if the cursor is at the edge and invokes the appropriate callback.

        Args:
            movement_history (list): A list of recent (x, y) positions of the cursor.
            x (float | int): Current x position of the cursor.
            y (float | int): Current y position of the cursor.
            screen_size (tuple): A tuple representing the screen size (width, height).
        """
        edge = self.is_at_edge(movement_history, x, y, screen_size)
        if edge and edge in callbacks:
            callbacks[edge]()

