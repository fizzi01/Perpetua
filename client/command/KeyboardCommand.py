import logging

from utils.command.Command import Command
from utils.Interfaces import IControllerContext
from utils.data import KeyboardData


class KeyboardCommand(Command):

    DESCRIPTION = "keyboard"

    def execute(self):
        # Silence logging for this command
        logging.getLogger().setLevel(logging.ERROR)

        # Use structured data object if available, fallback to legacy payload
        if self.has_data_object() and isinstance(self.data_object, KeyboardData):
            key = self.data_object.key
            event = self.data_object.event
        elif self.has_legacy_payload() and self.payload and len(self.payload) >= 2:
            key, event = self.payload[1], self.payload[0]
        else:
            logging.error(f"({self.DESCRIPTION}) error: invalid data source")
            return

        if isinstance(self.context, IControllerContext):
            keyboard_ctrl = self.context.keyboard_controller

            if keyboard_ctrl:
                keyboard_ctrl.process_key_command(key, event)
                logging.debug(f"({self.DESCRIPTION}) Keyboard command processed: {key}, {event}")
