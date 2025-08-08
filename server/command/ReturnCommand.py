import logging

from utils.command.Command import Command
from utils.Interfaces import IEventBus
from utils.data import ReturnData


class ReturnCommand(Command):

    DESCRIPTION = "return"

    def execute(self):
        logging.info(f"({self.DESCRIPTION}) Executing command")
        active_screen = self.context.get_active_screen()
        position = self.context.get_current_mouse_position()
        
        # Use structured data object if available, fallback to legacy payload
        if self.has_data_object() and isinstance(self.data_object, ReturnData):
            direction = self.data_object.command
        elif self.has_legacy_payload() and self.payload:
            direction = self.payload[0]
        else:
            logging.error(f"({self.DESCRIPTION}) No valid data source available")
            return

        if active_screen == "left" and direction == "right":
            self.event_bus.publish(IEventBus.SCREEN_RESET_EVENT, direction=direction, position=position)
        elif active_screen == "right" and direction == "left":
            self.event_bus.publish(IEventBus.SCREEN_RESET_EVENT, direction=direction, position=position)
        elif active_screen == "up" and direction == "down":
            self.event_bus.publish(IEventBus.SCREEN_RESET_EVENT, direction=direction, position=position)
        elif active_screen == "down" and direction == "up":
            self.event_bus.publish(IEventBus.SCREEN_RESET_EVENT, direction=direction, position=position)
