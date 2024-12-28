import time

from server.screen.ScreenReset import ScreenResetStrategyFactory

from utils.Interfaces import IClientContext, IHandler, IInputListenerService, IMouseController, IClipboardController, \
    IInputControllerService, IControllerContext, IScreenContext, IServerScreenMouseService, IMouseListener
from utils.Logging import Logger


class InputListenerService(IInputListenerService):
    def __init__(self,
                 context: IClientContext,
                 mouse_listener: IHandler | IMouseListener,
                 keyboard_listener: IHandler,
                 clipboard_listener: IHandler,
                 logger: Logger):

        super().__init__()

        self.context = context
        self.logger = logger

        # Factory per creare i listener
        self.mouse_listener: IMouseListener = mouse_listener
        self.keyboard_listener: IHandler = keyboard_listener
        self.clipboard_listener: IHandler = clipboard_listener

        self.listeners = [self.keyboard_listener, self.mouse_listener, self.clipboard_listener]

    def start(self):
        self.logger.log("Starting Input Listeners...", Logger.DEBUG)
        for listener in self.listeners:
            listener.start()
            # wait for listener to start (On macOS, listeners are not started immediately and should not start at the same time)
            time.sleep(0.1)
            if not listener.is_alive():
                raise Exception(f"{listener} not started.")
        self.logger.log("All Input Listeners started.", Logger.DEBUG)

    def join(self, timeout: int = 5):
        for listener in self.listeners:
            listener.stop()
        self.logger.log("InputService stopped.", Logger.DEBUG)

    def is_alive(self) -> bool:
        return all([listener.is_alive() for listener in self.listeners])

    def get_mouse_position(self) -> tuple:
        """
        Get the current virtual mouse position
        :return: Tuple with x and y coordinates
        """
        return self.mouse_listener.get_position()


class InputControllerService(IInputControllerService):
    def __init__(self,
                 mouse_controller: IMouseController | None = None,
                 clipboard_controller: IClipboardController | None = None,
                 keyboard_controller: IHandler | None = None):
        super().__init__()

        self.mouse_controller = mouse_controller
        self.clipboard_controller = clipboard_controller
        self.keyboard_controller = keyboard_controller

        self._started = False

    def get_mouse_controller(self):
        return self.mouse_controller

    def get_clipboard_controller(self):
        return self.clipboard_controller

    def get_keyboard_controller(self):
        return self.keyboard_controller

    def start(self) -> None:
        self._started = True
        self.mouse_controller.start()

    def join(self, timeout: int = 0) -> None:
        self._started = False
        if self.mouse_controller.is_alive():
            self.mouse_controller.stop()

    def stop(self) -> None:
        self._started = False

    def is_alive(self) -> bool:
        return self._started


class ScreenMouseService(IServerScreenMouseService):
    def __init__(self, context: IControllerContext | IScreenContext):
        super().__init__()

        self.context = context

        self.logger = Logger.get_instance().log

    def start(self) -> None:
        pass

    def join(self, timeout: int = 0) -> None:
        pass

    def stop(self) -> None:
        pass

    def is_alive(self) -> bool:
        pass

    def reset_mouse(self, direction: str, pos: float):
        strategy = ScreenResetStrategyFactory.get_reset_strategy(direction, self.force_mouse_position,
                                                                 context=self.context)
        strategy.reset(pos)

    def force_mouse_position(self, x: float, y: float):
        desired_position = (x, y)
        attempt = 0
        max_attempts = 600
        update_interval = 0.05
        last_update_time = time.time()

        mouse_controller = self.context.mouse_controller

        # Presuppone che self.context abbia un mouse_controller
        while not self.is_mouse_position_reached(desired_position) and attempt < max_attempts:
            time.sleep(0.01)
            current_time = time.time()
            if current_time - last_update_time >= update_interval:
                mouse_controller.set_position(desired_position[0], desired_position[1])
                attempt += 1
                last_update_time = current_time

    def is_mouse_position_reached(self, desired_position: tuple[float, float], margin: float = 50) -> bool:
        mouse_controller = self.context.mouse_controller
        current_position = mouse_controller.get_current_position()
        return (abs(current_position[0] - desired_position[0]) <= margin and
                abs(current_position[1] - desired_position[1]) <= margin)
