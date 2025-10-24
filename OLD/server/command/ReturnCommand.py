import logging

from utils.command.Command import Command
from utils.Interfaces import IEventBus


class ReturnCommand(Command):

    DESCRIPTION = "return"

    def execute(self):
        logging.info(f"({self.DESCRIPTION}) Executing command")
        active_screen = self.context.get_active_screen()
        direction = self.payload[0]
        position = self.context.get_current_mouse_position()

        if active_screen == "left" and direction == "right":
            self.event_bus.publish(IEventBus.SCREEN_RESET_EVENT, direction=direction, position=position)
        elif active_screen == "right" and direction == "left":
            self.event_bus.publish(IEventBus.SCREEN_RESET_EVENT, direction=direction, position=position)
        elif active_screen == "up" and direction == "down":
            self.event_bus.publish(IEventBus.SCREEN_RESET_EVENT, direction=direction, position=position)
        elif active_screen == "down" and direction == "up":
            self.event_bus.publish(IEventBus.SCREEN_RESET_EVENT, direction=direction, position=position)
