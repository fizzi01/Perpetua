import logging

from utils.command.Command import Command
from utils.Interfaces import IControllerContext


class KeyboardCommand(Command):

    DESCRIPTION = "keyboard"

    def execute(self):
        # Silence logging for this command
        logging.getLogger().setLevel(logging.ERROR)

        if len(self.payload) >= 2:
            key, event = self.payload[1], self.payload[0]
        else:
            logging.error(f"({self.DESCRIPTION}) error: invalid payload")
            return

        if isinstance(self.context, IControllerContext):
            keyboard_ctrl = self.context.keyboard_controller

            if keyboard_ctrl:
                keyboard_ctrl.process_key_command(key, event)
                logging.debug(f"({self.DESCRIPTION}) Keyboard command processed: {key}, {event}")
