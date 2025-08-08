import logging

from utils.command.Command import Command
from utils.Interfaces import IControllerContext


class ClipboardCommand(Command):

    DESCRIPTION = "clipboard"

    def execute(self):
        logging.debug(f"({self.DESCRIPTION}) Executing command")
        text = self.payload[0]  # se payload è [testo_clipboard]

        if isinstance(self.context, IControllerContext):
            clip_ctrl = self.context.clipboard_controller

            if clip_ctrl:
                clip_ctrl.set_clipboard_data(text)
                logging.debug(f"({self.DESCRIPTION}) Clipboard data set to: {text}")
