"""
Async-compatible connection wrapper for client streams.
Replaces BaseSocket for asyncio-based connections.
"""
import asyncio
from typing import Dict, Optional, Tuple


class AsyncClientConnection:
    """
    Manages multiple asyncio streams for a client connection.

    Compatible with asyncio StreamReader/StreamWriter instead of raw sockets.
    """

    def __init__(self, client_addr: Tuple[str, int]):
        self.client_addr = client_addr
        self.readers: Dict[int, asyncio.StreamReader] = {}
        self.writers: Dict[int, asyncio.StreamWriter] = {}

    def add_stream(self, stream_type: int, reader: asyncio.StreamReader,
                   writer: asyncio.StreamWriter):
        """Add a stream of the given type"""
        self.readers[stream_type] = reader
        self.writers[stream_type] = writer

    def get_reader(self, stream_type: int) -> Optional[asyncio.StreamReader]:
        """Get reader for stream type"""
        return self.readers.get(stream_type)

    def get_writer(self, stream_type: int) -> Optional[asyncio.StreamWriter]:
        """Get writer for stream type"""
        return self.writers.get(stream_type)


    def get_stream(self, stream_type: int) -> tuple[Optional[asyncio.StreamReader], Optional[asyncio.StreamWriter]]:
        """Get both reader and writer for stream type"""
        return self.readers.get(stream_type), self.writers.get(stream_type)

    def has_stream(self, stream_type: int) -> bool:
        """Check if stream type exists"""
        return stream_type in self.writers

    def is_open(self) -> bool:
        """
        Check if connection is still open by checking if any writer is not closing.

        Returns:
            True if at least one stream is open
        """
        if not self.writers and not self.readers:
            return False

        # Check if any writer is not closing
        for writer in self.writers.values():
            if writer and not writer.is_closing():
                return True

        return False

    def close(self):
        """Close all streams"""
        for writer in self.writers.values():
            if writer and not writer.is_closing():
                writer.close()

        for reader in self.readers.values():
            if reader:
                reader.feed_eof()

    async def wait_closed(self):
        """Wait for all streams to close"""
        tasks = []
        for writer in list(self.writers.values()):
            if writer and not writer.is_closing():
                writer.close()
                tasks.append(writer.wait_closed())

        for reader in list(self.readers.values()):
            if reader:
                reader.feed_eof()

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

            self.readers.clear()
            self.writers.clear()

