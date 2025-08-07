import logging

from utils.command.Command import Command
from utils.Interfaces import IControllerContext, IBaseCommand
from utils.command.ClipboardCommand import ClipboardCommand as ClipboardDataCommand


class ClipboardCommand(Command):

    DESCRIPTION = "clipboard"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.base_command = kwargs.get('base_command', None)

    def execute(self):
        logging.debug(f"({self.DESCRIPTION}) Executing command")
        
        # Get text from IBaseCommand object or legacy payload
        if isinstance(self.base_command, ClipboardDataCommand):
            text = self.base_command.content
        elif self.payload and len(self.payload) > 0:
            text = self.payload[0]  # Legacy payload support
        else:
            logging.error(f"({self.DESCRIPTION}) No clipboard content provided")
            return

        if isinstance(self.context, IControllerContext):
            clip_ctrl = self.context.clipboard_controller

            if clip_ctrl:
                clip_ctrl.set_clipboard_data(text)
                logging.debug(f"({self.DESCRIPTION}) Clipboard data set to: {text}")
