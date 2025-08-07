import logging

from client.state.ClientState import ControlledState
from utils.command.Command import Command
from utils.Interfaces import IControllerContext, IClientContext, IBaseCommand
from utils.command.MouseCommand import MouseCommand as MouseDataCommand


class MouseCommand(Command):

    DESCRIPTION = "mouse"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.base_command = kwargs.get('base_command', None)

    def execute(self):
        # Silence logging for this command
        logging.getLogger().setLevel(logging.ERROR)

        # If client_state is Hidden, set it to Controlled
        if isinstance(self.context, IClientContext):
            if not self.context.is_state(ControlledState):
                logging.debug(f"({self.DESCRIPTION}) Setting client state to Controlled")
                self.context.set_state(ControlledState())

        # Extract data from IBaseCommand object or legacy payload
        if isinstance(self.base_command, MouseDataCommand):
            event = self.base_command.action
            x = self.base_command.x
            y = self.base_command.y
            is_pressed = getattr(self.base_command, 'is_pressed', False)
        elif self.payload and len(self.payload) >= 3:
            event, x, y = self.payload[0], self.payload[1], self.payload[2]
            is_pressed = self.payload[3] == "true" if len(self.payload) >= 4 else False
        else:
            logging.error(f"({self.DESCRIPTION}) error: invalid command data")
            return

        if isinstance(self.context, IControllerContext):
            mouse_ctrl = self.context.mouse_controller

            if mouse_ctrl:
                mouse_ctrl.process_mouse_command(x=x, y=y, mouse_action=event, is_pressed=is_pressed)
                logging.debug(f"({self.DESCRIPTION}) Mouse command processed: {event}, {x}, {y}, {is_pressed}")
