import logging

from utils.command.Command import Command
from utils.Interfaces import IEventBus, IBaseCommand
from utils.command.ReturnCommand import ReturnCommand as ReturnDataCommand


class ReturnCommand(Command):

    DESCRIPTION = "return"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.base_command = kwargs.get('base_command', None)

    def execute(self):
        logging.info(f"({self.DESCRIPTION}) Executing command")
        active_screen = self.context.get_active_screen()
        
        # Extract direction from IBaseCommand object or legacy payload
        if isinstance(self.base_command, ReturnDataCommand):
            direction = self.base_command.direction
        elif self.payload and len(self.payload) > 0:
            direction = self.payload[0]  # Legacy payload support
        else:
            logging.error(f"({self.DESCRIPTION}) No direction provided")
            return
            
        position = self.context.get_current_mouse_position()

        if active_screen == "left" and direction == "right":
            self.event_bus.publish(IEventBus.SCREEN_RESET_EVENT, direction=direction, position=position)
        elif active_screen == "right" and direction == "left":
            self.event_bus.publish(IEventBus.SCREEN_RESET_EVENT, direction=direction, position=position)
        elif active_screen == "up" and direction == "down":
            self.event_bus.publish(IEventBus.SCREEN_RESET_EVENT, direction=direction, position=position)
        elif active_screen == "down" and direction == "up":
            self.event_bus.publish(IEventBus.SCREEN_RESET_EVENT, direction=direction, position=position)
