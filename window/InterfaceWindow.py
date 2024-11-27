from abc import ABC, abstractmethod


class AbstractHiddenWindow(ABC):

    def _start_window_app(self, input_conn, output_conn):
        pass

    def _window_proc_controller(self, input_conn, output_conn):
        pass

    def send_command(self, command):
        pass

    @abstractmethod
    def close(self):
        pass

    @abstractmethod
    def show(self):
        pass

    @abstractmethod
    def stop(self):
        pass

    @abstractmethod
    def minimize(self):
        pass

    @abstractmethod
    def maximize(self):
        pass

    @abstractmethod
    def wait(self, timeout=5):
        pass
