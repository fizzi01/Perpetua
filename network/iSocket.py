from abc import ABC, abstractmethod


class AbstractSocket(ABC):

    def __init__(self, socket):
        self.sock = socket

    @abstractmethod
    def connect(self, host, port):
        pass

    @abstractmethod
    def send(self, data):
        pass

    @abstractmethod
    def receive(self, buffer_size):
        pass

    @abstractmethod
    def close(self):
        pass
