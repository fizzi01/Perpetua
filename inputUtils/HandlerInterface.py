from abc import ABC, abstractmethod


class HandlerInterface(ABC):
    @abstractmethod
    def start(self):
        pass

    @abstractmethod
    def stop(self):
        pass


class MouseListenerHandler(HandlerInterface):
    def start(self):
        print("Mouse Listener Handler Started")

    def stop(self):
        print("Mouse Listener Handler Stopped")
