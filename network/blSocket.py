
from .iSocket import AbstractSocket


class BluetoothSocket(AbstractSocket):
    def __init__(self):
        super().__init__(None)

    def connect(self, addr, port):
        pass

    def send(self, data):
        pass

    def receive(self, buffer_size):
        pass

    def close(self):
        pass