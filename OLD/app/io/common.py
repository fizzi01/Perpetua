from multiprocessing import Pipe
from queue import Queue
from typing import Optional


class ProcessMessage:
    def __init__(self, input_conn: Pipe, output_conn: Pipe):
        self.input_conn = input_conn
        self.output_conn = output_conn

    def input(self, msg: Optional[str] = None):
        msg = msg if msg is not None else ""
        self.input_conn.send(msg)
        return self.input_conn.recv()

    def print(self, msg):
        self.output_conn.send(msg)

    def read(self):
        return self.output_conn.recv()


class QueueLogger:
    def __init__(self, queue: Optional[Queue] = None):
        self.queue = queue
        self.closed = False

    def write(self, msg):
        self.queue.put(msg)

    def flush(self):
        while not self.queue.empty():
            print(self.queue.get())

    def read(self, timeout=1):
        return self.queue.get(timeout=timeout)

    def close(self):
        self.queue.put(None)
        self.closed = True