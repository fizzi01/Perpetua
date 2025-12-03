import socket
from typing import Optional, Dict
from socket import timeout, error

from utils.logging import Logger
from utils.override import override


class BaseSocket(socket.socket):
    # Maschera il socket di base per consentire l'accesso ai metodi di socket
    def __getattr__(self, item):
        return getattr(self.socket, item)

    def __init__(self, address: tuple = ("", 0)):
        """
        Inizializza il BaseSocket
        :param address: Indirizzo del socket
        """
        super().__init__()
        self.streams: Dict[int, socket.socket] = {}

        self._address = address
        self.log = Logger.get_instance().log

    def get_stream(self, stream_type: int) -> socket.socket:
        """
        Restituisce lo stream associato al tipo di stream specificato
        :param stream_type: Tipo di stream (StreamType)
        :return: Socket associato allo stream
        """
        return self.streams.get(stream_type)

    def put_stream(self, stream_type: int, stream_socket: socket.socket) -> 'BaseSocket':
        """
        Aggiunge uno stream al socket
        :param stream_type: Tipo di stream (StreamType)
        :param stream_socket: Socket associato allo stream
        """
        self.streams[stream_type] = stream_socket
        return self

    def close_stream(self, stream_type: int) -> 'BaseSocket':
        if stream_type in self.streams:
            try:
                self.streams[stream_type].close()
            except EOFError:
                pass

        return self

    @property
    def address(self) -> tuple:
        return self._address

    @override
    def send(self, data: str | bytes, stream: Optional[int] = None):
        if isinstance(data, str):
            data = data.encode()

        try:
            if stream and self.streams[stream]:
                self.streams[stream].sendall(data)
        except EOFError:
            pass

    @override
    def recv(self, size: int, stream: Optional[int] = None) -> bytes:
        try:
            if stream and self.streams[stream]:
                return self.streams[stream].recv(size)
            else:
                return b""
        except EOFError:
            return b""

    def close(self, stream: Optional[int] = None):
        try:
            if stream:
                if stream in self.streams:
                    self.streams[stream].shutdown(socket.SHUT_RDWR)
                    self.streams[stream].close()
                    # Remove the stream from the dictionary after closing
                    del self.streams[stream]
            else:
                for stream in self.streams.values():
                    stream.shutdown(socket.SHUT_RDWR)
                    stream.close()
                # Clear all streams after closing
                self.streams.clear()
        except EOFError:
            pass
        except ConnectionResetError:
            pass
        except BrokenPipeError:
            pass
        except OSError:
            pass
        except ConnectionAbortedError:
            pass

    def is_socket_open(self):
        try:
            # Check only the first stream for simplicity if present
            if len(self.streams) == 0:
                return False

            # self.streams[next(iter(self.streams))].getpeername()
            for stream in self.streams:
                # put temporarly socker in not blocking mode
                self.streams[stream].setblocking(False)
                data = self.streams[stream].recv(16)
                self.streams[stream].setblocking(True)
                if len(data) == 0:
                    return False
            return True
        except BlockingIOError:
            return True
        except ConnectionResetError:
            return False
        except EOFError:
            return False
        except socket.error:
            return False
        except (timeout, error) as e:
            return False
        except Exception:
            import traceback
            traceback.print_exc()
            return False