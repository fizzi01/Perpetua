import logging

from utils.command.Command import Command
from utils.Interfaces import IControllerContext, IBaseCommand
from utils.command.KeyboardCommand import KeyboardCommand as KeyboardDataCommand


class KeyboardCommand(Command):

    DESCRIPTION = "keyboard"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.base_command = kwargs.get('base_command', None)

    def execute(self):
        # Silence logging for this command
        logging.getLogger().setLevel(logging.ERROR)

        # Extract data from IBaseCommand object or legacy payload
        if isinstance(self.base_command, KeyboardDataCommand):
            key = self.base_command.key
            event = self.base_command.action
        elif self.payload and len(self.payload) >= 2:
            key, event = self.payload[1], self.payload[0]
        else:
            logging.error(f"({self.DESCRIPTION}) error: invalid command data")
            return

        if isinstance(self.context, IControllerContext):
            keyboard_ctrl = self.context.keyboard_controller

            if keyboard_ctrl:
                keyboard_ctrl.process_key_command(key, event)
                logging.debug(f"({self.DESCRIPTION}) Keyboard command processed: {key}, {event}")
