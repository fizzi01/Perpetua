import logging

from client.state.ClientState import ControlledState
from utils.command.Command import Command
from utils.Interfaces import IControllerContext, IClientContext
from utils.data import MouseData


class MouseCommand(Command):

    DESCRIPTION = "mouse"

    def execute(self):
        # Silence logging for this command
        logging.getLogger().setLevel(logging.ERROR)

        # If client_state is Hidden, set it to Controlled
        if isinstance(self.context, IClientContext):
            if not self.context.is_state(ControlledState):
                logging.debug(f"({self.DESCRIPTION}) Setting client state to Controlled")
                self.context.set_state(ControlledState())

        # Use structured data object if available, fallback to legacy payload
        if self.has_data_object() and isinstance(self.data_object, MouseData):
            event = self.data_object.event
            x = self.data_object.x
            y = self.data_object.y
            is_pressed = self.data_object.is_pressed
        elif self.has_legacy_payload() and self.payload and len(self.payload) >= 3:
            event, x, y = self.payload[0], self.payload[1], self.payload[2]
            is_pressed = self.payload[3] == "true" if len(self.payload) >= 4 else False
        else:
            logging.error(f"({self.DESCRIPTION}) error: invalid data source")
            return

        if isinstance(self.context, IControllerContext):
            mouse_ctrl = self.context.mouse_controller

            if mouse_ctrl:
                mouse_ctrl.process_mouse_command(x=x, y=y, mouse_action=event, is_pressed=is_pressed)
                logging.debug(f"({self.DESCRIPTION}) Mouse command processed: {event}, {x}, {y}, {is_pressed}")
