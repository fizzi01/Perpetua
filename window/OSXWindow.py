import tkinter as tk
from Cocoa import NSApplication, NSApp, NSWindow, NSWindowStyleMaskBorderless, NSBackingStoreBuffered, NSMakeRect, \
    NSCursor
from Quartz.CoreGraphics import CGDisplayHideCursor, CGMainDisplayID, CGAssociateMouseAndMouseCursorPosition, \
    CGDisplayShowCursor
from AppKit import NSView, NSRectFill, NSZeroRect, NSColor, NSScreen, NSMaxY, NSApplicationPresentationOptions, \
    NSApplicationPresentationHideDock
import objc


class TransparentView(NSView):
    def drawRect_(self, rect):
        NSColor.clearColor().set()
        NSRectFill(rect)


class HiddenWindow:
    def __init__(self, parent=None):
        self.app = NSApplication.sharedApplication()

        # Ottieni le dimensioni dello schermo
        screen = NSScreen.mainScreen().frame()
        max_y = NSMaxY(screen)  # Coordinata Y massima dello schermo

        # Imposta le dimensioni e le coordinate della finestra
        rect = NSMakeRect(0, 0, screen.size.width, screen.size.height + 100)  # Estende la finestra al di sopra del dock
        rect.origin.y = max_y - rect.size.height

        styleMask = NSWindowStyleMaskBorderless
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(rect, styleMask,
                                                                                    NSBackingStoreBuffered, False)
        self.window.setLevel_(
            CGMainDisplayID() * 1000)  # Imposta il livello della finestra a un livello elevato per non interagire con il menu di sistema e con lo stage manager
        self.window.setBackgroundColor_(NSColor.clearColor())
        self.window.setOpaque_(False)
        self.window.setHasShadow_(False)
        self.window.setIgnoresMouseEvents_(False)

        view = TransparentView.alloc().initWithFrame_(rect)
        self.window.setContentView_(view)

        self.window.makeKeyAndOrderFront_(None)
        self.app.activateIgnoringOtherApps_(True)

        self.minimize()
        # Nascondi il cursore
        #CGDisplayHideCursor(CGMainDisplayID())

        # Disabilita l'interazione del cursore con il sistema
        #CGAssociateMouseAndMouseCursorPosition(False)

    def minimize(self):
        # Mostra il cursore quando si minimizza la finestra
        self.app.setPresentationOptions_(0)
        CGDisplayShowCursor(CGMainDisplayID())
        self.window.orderOut_(None)

    def maximize(self):
        # Nascondi il cursore
        # Nasconde il Dock
        self.app.setPresentationOptions_(NSApplicationPresentationHideDock)
        CGDisplayHideCursor(CGMainDisplayID())
        self.window.makeKeyAndOrderFront_(None)

    def close(self):
        # Close the window
        self.app.setPresentationOptions_(0)
        CGDisplayShowCursor(CGMainDisplayID())
        self.window.orderOut_(None)
        self.window.close()


"""class TransparentFullscreenWindow(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)

        self.root = self
        self.configure_window()

        # State of the window
        self.is_open = False
        self.is_fullscreen = False

    def configure_window(self):
        #Configure the window based on the operating system.
        # Make the window fullscreen
        # self.root.attributes('-fullscreen', True)
        # Start minimized
        self.root.iconify()
        self.root.overrideredirect(True)  # Remove window decorations

        self.root.wm_attributes('-topmost', True)  # Make the root window always on top
        self.root.wm_attributes('-transparent', True)  # Enable transparency
        self.root.config(bg='systemTransparent')  # Set the window background to transparent

        # Hide the mouse cursor
        self.root.config(cursor='none')

    def handle_close(self, event=None):
        # Close the window
        self.root.destroy()

    def minimize(self):
        return
        self.root.overrideredirect(False)
        self.root.iconify()
        self.root.overrideredirect(True)
        self.is_fullscreen = False

    def maximize(self):
        return
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
            self.is_open = False"""
