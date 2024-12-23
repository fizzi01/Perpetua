import screeninfo


def get_screen_size():
    screen = screeninfo.get_monitors()[0]
    if screen:
        return screen.width, screen.height
    else:
        return 0, 0
