import tkinter as tk

class TransparentNoCursorWindow:
    def __init__(self):
        # Create a root window
        self.root = tk.Tk()

        # Make the window fullscreen
        self.root.attributes('-fullscreen', True)

        # Remove the window border and title bar
        self.root.overrideredirect(True)

        # Make the window transparent
        self.root.attributes('-alpha', 0.01)  # Set the transparency level. 0.0 is fully transparent, 1.0 is opaque
        # Bind the escape key to the close method
        self.root.bind('<Escape>', self.handle_close)
        # Hide the mouse cursor
        self.root.config(cursor='none')

    def handle_close(self, event=None):
        # Close the window
        self.root.destroy()
    def run(self):
        # Start the Tkinter mainloop
        self.root.mainloop()

    def close(self):
        # Close the window
        self.root.destroy()

window = TransparentNoCursorWindow()
window.run()