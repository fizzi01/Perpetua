# client/ClientState.py
import threading
import time
from abc import ABC, abstractmethod


class State(ABC):
    @abstractmethod
    def handle(self):
        pass


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


class ClientState:
    _instance = None
    _lock = threading.Lock()
    COOLDOWN_PERIOD = 0.1

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super(ClientState, cls).__new__(cls)
                    cls._instance._state = HiddleState()  # Initial state is Hiddle
                    cls._instance._last_hiddle_time = 0
        return cls._instance

    def set_state(self, state: State):
        with self._lock:
            if isinstance(state, ControlledState):
                current_time = time.time()
                if current_time - self._instance._last_hiddle_time < self.COOLDOWN_PERIOD:
                    return
            if isinstance(state, HiddleState):
                self._last_hiddle_time = time.time()
            self._state = state

    def get_state(self):
        with self._lock:
            return self._state.handle()

    def is_controlled(self):
        with self._lock:
            return isinstance(self._state, ControlledState)
