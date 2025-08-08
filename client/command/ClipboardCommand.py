import logging

from utils.command.Command import Command
from utils.Interfaces import IControllerContext
from utils.data import ClipboardData


class ClipboardCommand(Command):

    DESCRIPTION = "clipboard"

    def execute(self):
        logging.debug(f"({self.DESCRIPTION}) Executing command")
        
        # Use structured data object if available, fallback to legacy payload
        if self.has_data_object() and isinstance(self.data_object, ClipboardData):
            text = self.data_object.content
        elif self.has_legacy_payload() and self.payload:
            text = self.payload[0]  # Legacy: payload is [text_clipboard]
        else:
            logging.error(f"({self.DESCRIPTION}) No valid data source available")
            return

        if isinstance(self.context, IControllerContext):
            clip_ctrl = self.context.clipboard_controller

            if clip_ctrl:
                clip_ctrl.set_clipboard_data(text)
                logging.debug(f"({self.DESCRIPTION}) Clipboard data set to: {text}")
