import threading
import time

from utils.Interfaces import State, IClientStateService


class ControlledState(State):
    CONTROLLER_DELAY = 0.2

    def __init__(self):
        self._timer_started = True
        self._start_timer()

    def _start_timer(self):
        threading.Thread(target=self._timer_thread, daemon=True).start()

    def _timer_thread(self):
        self._timer_started = True
        time.sleep(self.CONTROLLER_DELAY)
        self._timer_started = False

    def handle(self):
        # Implement the behavior for the Controlled state
        return not self._timer_started


class HiddleState(State):

    def handle(self):
        # If the client is in Hiddle state, the client cannot send return commands to the server
        return False


class ClientState(IClientStateService):
    COOLDOWN_PERIOD = 0.1

    def __init__(self):
        self._lock = threading.Lock()
        self._state = HiddleState()  # Initial state is Hiddle
        self._last_hiddle_time = 0

    def set_state(self, state: State):
        with self._lock:

            if isinstance(state, ControlledState):
                current_time = time.time()
                if current_time - self._last_hiddle_time < self.COOLDOWN_PERIOD:
                    return
            if isinstance(state, HiddleState):
                self._last_hiddle_time = time.time()

            self._state = state

    def get_state(self):
        with self._lock:
            return self._state.handle()

    def is_state(self, state: State.__class__) -> bool:
        with self._lock:
            return isinstance(self._state, state)

    def is_controlled(self):
        with self._lock:
            return isinstance(self._state, ControlledState)
