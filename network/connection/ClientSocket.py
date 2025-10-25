import asyncio
import ssl
from typing import Optional, Callable

from aioquic.asyncio import connect
from aioquic.quic.configuration import QuicConfiguration

from network.connection.GeneralSocket import QuicSocket, QuicProtocol
from utils.logging.logger import Logger


class QuicClientSocket(QuicSocket):
    """Socket client QUIC per connettersi a un server"""

    def __init__(self, host: str, port: int):
        super().__init__(host, port)
        self._connection_task: Optional[asyncio.Task] = None
        self._on_connected: Optional[Callable] = None
        self._on_stream_data_received: Optional[Callable] = None

    def set_callbacks(self,
                      on_connected: Optional[Callable] = None,
                      on_stream_data_received: Optional[Callable] = None):
        """Imposta callback per eventi del client"""
        self._on_connected = on_connected
        self._on_stream_data_received = on_stream_data_received

    async def start(self):
        """Avvia la connessione al server"""
        configuration = QuicConfiguration(
            is_client=True,
            alpn_protocols=["hq-interop"],
        )
        configuration.verify_mode = ssl.CERT_NONE  # Per development

        async with connect(
                self.host,
                self.port,
                configuration=configuration,
                create_protocol=self._create_protocol,
        ) as protocol:
            self.protocol = protocol
            await self._connection_loop()

    def _create_protocol(self, *args, **kwargs) -> QuicProtocol:
        """Factory per creare il protocollo QUIC"""
        protocol = QuicProtocol(*args, **kwargs)
        protocol.on_stream_data = self._handle_stream_data
        protocol.on_connection_ready = self._on_connection_ready

        return protocol

    async def _on_connection_ready(self, protocol: QuicProtocol):
        """Callback quando la connessione è stabilita"""
        self.logger.log("[QUIC] Connected to server", Logger.INFO)

        if self._on_connected:
            await self._on_connected(protocol)

    async def _handle_stream_data(self, stream_id: int, data: bytes, end_stream: bool):
        """Gestisce dati ricevuti su uno stream"""
        if self._on_stream_data_received:
            await self._on_stream_data_received(stream_id, data, end_stream)

    async def _connection_loop(self):
        """Loop principale per mantenere la connessione attiva"""
        try:
            await asyncio.Future()  # Rimane in attesa
        except asyncio.CancelledError:
            pass

    async def close(self):
        """Chiude la connessione client"""
        await super().close()
        self.logger.log("[QUIC] Client disconnected", Logger.INFO)
