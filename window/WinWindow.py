import tkinter as tk
from sys import platform

from window.InterfaceWindow import AbstractHiddenWindow


class TransparentFullscreenWindow(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)

        self.root = self
        self.configure_window()

        # State of the window
        self.is_open = False
        self.is_fullscreen = False

    def configure_window(self):
        """Configure the window based on the operating system."""
        # Make the window fullscreen
        # self.root.attributes('-fullscreen', True)
        # Start minimized
        self.root.iconify()
        self.root.overrideredirect(True)  # Remove window decorations

        if platform.startswith('linux'):
            self.root.wait_visibility(self.root)
            self.root.wm_attributes('-alpha', 0.01)  # Set the transparency level

        elif platform == 'darwin':
            self.root.wm_attributes('-topmost', True)  # Make the root window always on top
            self.root.wm_attributes('-transparent', True)  # Enable transparency
            self.root.config(bg='systemTransparent')  # Set the window background to transparent

        elif platform == 'win32':
            # Make the window transparent
            self.root.attributes('-alpha', 0.01)  # Set the transparency level. 0.0 is fully transparent, 1.0 is opaque
            # Bind the escape key to the close method
            # self.root.bind('<Escape>', self.handle_close)

        # Hide the mouse cursor
        self.root.config(cursor='none')

    def handle_close(self, event=None):
        # Close the window
        self.root.destroy()

    def minimize(self):
        self.root.overrideredirect(False)
        self.root.iconify()
        self.root.overrideredirect(True)
        self.is_fullscreen = False

    def maximize(self):
        self.root.deiconify()
        self.root.overrideredirect(False)
        self.root.attributes('-fullscreen', True)
        self.root.lift()
        self.root.attributes('-topmost', True)
        self.root.overrideredirect(True)

    def toggle(self):
        # Toggle window state between minimized and fullscreen
        if self.is_fullscreen:
            print("Minimizing window")
            self.root.overrideredirect(False)
            self.root.iconify()
            self.root.overrideredirect(True)
            self.is_fullscreen = False
        else:
            print("Expanding window")
            self.root.deiconify()
            self.root.overrideredirect(False)
            self.root.attributes('-fullscreen', True)
            self.root.overrideredirect(True)
            self.root.lift()  # Bring window to the top of the window stack
            self.is_fullscreen = True

    def run(self):
        # Start the Tkinter mainloop
        self.is_open = True
        #TransparentFullscreenWindow(self.root)
        self.is_open = False

    def close(self):
        # Close the window
        if self.is_open:
            self.destroy()
            self.is_open = False


class DebugWindow(AbstractHiddenWindow):
    def close(self):
        pass

    def show(self):
        pass

    def stop(self):
        pass

    def minimize(self):
        pass

    def maximize(self):
        pass

    def wait(self, timeout=5):
       return True