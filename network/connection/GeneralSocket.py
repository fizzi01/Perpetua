import asyncio
import socket
from typing import Optional, Callable, Dict
from abc import ABC, abstractmethod
from aioquic.asyncio import QuicConnectionProtocol
from aioquic.quic.events import StreamDataReceived, HandshakeCompleted, ConnectionTerminated
from enum import IntEnum

from attr import dataclass

from utils.logging.logger import Logger
from utils.override import override

@dataclass
class StreamType:
    """Tipi di stream QUIC con priorità"""
    COMMAND = 0      # Alta priorità - comandi bidirezionali
    KEYBOARD = 4     # Alta priorità - eventi tastiera
    MOUSE = 1        # Media priorità - movimenti mouse (alta frequenza)
    CLIPBOARD = 12   # Bassa priorità - clipboard
    FILE = 16        # Bassa priorità - trasferimenti file


class QuicProtocol(QuicConnectionProtocol):
    """Protocollo QUIC personalizzato per gestire stream multipli"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.on_stream_data: Optional[Callable] = None
        self.on_connection_ready: Optional[Callable] = None
        self.on_connection_lost: Optional[Callable] = None
        self.logger = Logger.get_instance()
        self._connection_id = None

    def quic_event_received(self, event):
        """Gestisce eventi QUIC in arrivo"""
        if isinstance(event, HandshakeCompleted):
            self.logger.log("[QUIC] Handshake completed", Logger.DEBUG)
            if self.on_connection_ready:
                asyncio.create_task(self.on_connection_ready(self))

        elif isinstance(event, StreamDataReceived):
            if self.on_stream_data:
                asyncio.create_task(
                    self.on_stream_data(
                        event.stream_id,
                        event.data,
                        event.end_stream
                    )
                )

        elif isinstance(event, ConnectionTerminated):
            self.logger.log(
                f"[QUIC] Connection terminated: {event.error_code}",
                Logger.WARNING
            )
            if self.on_connection_lost:
                asyncio.create_task(self.on_connection_lost())

    def send_stream_data(self, stream_type: int, data: bytes):
        """Invia dati su uno stream specifico"""
        stream_id = stream_type
        self._quic.send_stream_data(stream_id, data, end_stream=False)
        self.transmit()


class QuicSocket(ABC):
    """Classe astratta per gestire connessioni QUIC"""

    def __init__(self,
                 host: str = "0.0.0.0",
                 port: int = 5001):
        self.host = host
        self.port = port
        self.logger = Logger.get_instance()
        self.protocol: Optional[QuicProtocol] = None

    @abstractmethod
    async def start(self):
        """Avvia la connessione QUIC"""
        pass

    def send_on_stream(self, stream_type: int, data: bytes):
        """Invia dati su uno stream specifico"""
        if not self.protocol:
            raise RuntimeError("QUIC connection not established")
        self.protocol.send_stream_data(stream_type, data)

    def is_socket_open(self) -> bool:
        """Verifica se la connessione è attiva"""
        return True # TODO: Implementare il controllo dello stato della connessione

    async def close(self):
        """Chiude la connessione QUIC"""
        if self.protocol:
            self.protocol.close()


class BaseSocket(socket.socket):
    # Maschera il socket di base per consentire l'accesso ai metodi di socket
    def __getattr__(self, item):
        return getattr(self.socket, item)

    def __init__(self, address: str = ""):
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
    def address(self) -> str:
        return self._address

    @override
    def send(self, data: str | bytes, stream: Optional[int] = None):
        if isinstance(data, str):
            data = data.encode()

        try:
            if stream and self.streams[stream]:
                self.streams[stream].send(data)
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

    def close(self):
        try:
            for stream in self.streams.values():
                stream.close()
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
            # Check only the first stream for simplicity
            self.streams[next(iter(self.streams))].getpeername()
            return True
        except socket.error:
            return False