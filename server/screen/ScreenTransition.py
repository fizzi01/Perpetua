import threading
from typing import Optional

from utils.Logging import Logger
from utils.Interfaces import IServerContext, IScreenTransitionController

from window.InterfaceWindow import AbstractHiddenWindow
from window import Window

from server.screen.ScreenState import ScreenStateFactory


class ScreenTransitionController(IScreenTransitionController):
    def __init__(self, context: IServerContext):
        super().__init__()

        self.context = context
        self.window: AbstractHiddenWindow = Window()
        self._is_transition = False

        self.changed = threading.Event()
        self.block_transition = threading.Event()
        self.transition_completed = threading.Event()

        self._checker = threading.Thread(target=self._check_screen_transition, daemon=True)
        self._securer = threading.Thread(target=self._secure_transaction, daemon=True)
        self._running = False
        self.lock = threading.RLock()

    def start(self):
        self.window.start()
        if not self.window.wait(timeout=2):
            raise Exception("Can't initialize screen transition controller")
        self.window.minimize()

        self._running = True
        self._checker.start()
        self._securer.start()
        self.context.log("ScreenTransitionController started.", Logger.DEBUG)

    def join(self, timeout: int = 2):
        self._running = False
        self.changed.set()
        self.transition_completed.set()
        self.block_transition.clear()
        self._checker.join(timeout=timeout)
        self._securer.join(timeout=timeout)

        if self.window:
            self.window.close()

        # Check threads status
        if not self._checker.is_alive() and not self._securer.is_alive():
            self._running = False

        self.context.log("ScreenTransitionController stopped.", Logger.DEBUG)

    def is_alive(self):
        return self._checker.is_alive() and self._securer.is_alive()

    def mark_transition(self):
        self._is_transition = False
        self.changed.set()

    def mark_transition_completed(self):
        self.transition_completed.set()

    def mark_transition_blocked(self):
        self.block_transition.set()

    def change_screen(self, screen: Optional[str]) -> None:
        """Esegue la logica di cambio schermo """
        state = ScreenStateFactory.get_screen_state(screen, self.context)
        with self.lock:
            state.handle()

    def reset_screen(self, direction: str, position: tuple) -> None:
        """Esegue la logica di ritorno dello schermo """
        state = ScreenTransitionFactory.get_transition_state(context=self.context, direction=direction)
        with self.lock:
            self.context.log(f"Resetting screen from {direction} direction", Logger.INFO)

            self.context.set_active_screen(None)
            self.mark_transition()
            state.handle_transition()

    def is_transition_in_progress(self) -> bool:
        return self._is_transition if not self.block_transition.is_set() else False

    def is_transition_blocked(self) -> bool:
        return self.block_transition.is_set()

    def _check_screen_transition(self):
        while self._running:
            self.changed.wait()
            if not self._running:
                break
            self.context.log("[CHECKER] Checking screen transition...")
            if self.changed.is_set():
                active_screen = self.context.get_active_screen()
                self.context.log(f"Changing screen to {active_screen}", Logger.INFO)
                self._screen_toggle(active_screen)
                self._is_transition = True
                self.transition_completed.set()
                self.context.log(f"[CHECKER] Screen transition to {active_screen} completed.")
                self.changed.clear()

    def _secure_transaction(self):
        while self._running:
            self.context.log("[SECURER] Waiting for screen transition to complete...", Logger.DEBUG)
            self.changed.wait()
            if not self._running:
                break
            self.block_transition.set()
            self.context.log("[SECURER] Blocking screen transition.", Logger.DEBUG)
            self.context.log("[SECURER] Waiting for transition to complete...", Logger.DEBUG)
            self.transition_completed.wait(timeout=5)
            self.context.log("[SECURER] Transition completed.", Logger.DEBUG)

            self.transition_completed.clear()
            self.block_transition.clear()
            self.changed.clear()
            self.context.log("[SECURER] Securer completed.", Logger.DEBUG)

    def _screen_toggle(self, screen):
        if self.window:
            if not screen:
                self.window.minimize()
            else:
                self.window.maximize()


class ScreenTransitionFactory:
    """
    Factory class for creating screen transition state objects based on the active screen.
    """

    @staticmethod
    def get_transition_state(context: IServerContext, direction: str) -> 'ScreenTransitionState':
        if direction == "left":
            return LeftScreenTransition(context)
        elif direction == "right":
            return RightScreenTransition(context)
        elif direction == "up":
            return UpScreenTransition(context)
        elif direction == "down":
            return DownScreenTransition(context)
        else:
            return NoScreenTransition(context)


class ScreenTransitionState:
    """
    Base class for screen transition states.
    """

    def __init__(self, context: IServerContext):
        self.context = context

    def handle_transition(self):
        raise NotImplementedError("Subclasses should implement this method.")


class LeftScreenTransition(ScreenTransitionState):
    def handle_transition(self):
        self.context.reset_mouse("right", self.context.get_current_mouse_position()[1])


class RightScreenTransition(ScreenTransitionState):
    def handle_transition(self):
        self.context.reset_mouse("left", self.context.get_current_mouse_position()[1])


class UpScreenTransition(ScreenTransitionState):
    def handle_transition(self):
        self.context.reset_mouse("down", self.context.get_current_mouse_position()[0])


class DownScreenTransition(ScreenTransitionState):
    def handle_transition(self):
        self.context.reset_mouse("up", self.context.get_current_mouse_position()[0])


class NoScreenTransition(ScreenTransitionState):
    def handle_transition(self):
        # No transition needed
        pass
