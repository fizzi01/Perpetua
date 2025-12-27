"""
Handles server and client socket logic
"""

import asyncio
from typing import Tuple, Dict, Optional


class StreamWrapper:
    """
    Wraps an asyncio StreamReader and StreamWriter pair.
    """

    class StreamReader:
        """
        Wrapper for asyncio StreamReader.
        """

        def __init__(self, reader: asyncio.StreamReader):
            self._reader: asyncio.StreamReader = reader

        async def recv(self, size: int) -> bytes:
            return await self._reader.read(size)

        def close(self):
            self._reader.feed_eof()

        def is_closed(self) -> bool:
            return self._reader.at_eof()

    class StreamWriter:
        """
        Wrapper for asyncio StreamWriter.
        """

        def __init__(self, writer: asyncio.StreamWriter):
            self._writer: asyncio.StreamWriter = writer

        async def send(self, data: bytes):
            self._writer.write(data)
            await self._writer.drain()

        async def close(self):
            self._writer.close()
            await self._writer.wait_closed()

        async def _try_writer_close(self):
            # Send empty data to trigger any pending operations
            try:
                self._writer.write(b"")
                await self._writer.drain()
            except Exception:
                pass

        async def is_closed(self) -> bool:
            """
            Checks if the writer connection is closed.

            This asynchronous method ensures that the writer connection is properly
            checked for closure. It attempts to close the writer if necessary and
            then verifies whether the writer connection is currently closing.

            Returns:
                bool: True if the writer connection is closing or closed, False otherwise.
            """
            await self._try_writer_close()
            return self._writer.is_closing()

        def get_sockname(self) -> Tuple[str, int]:
            return self._writer.get_extra_info("sockname", default=None)

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.reader = self.StreamReader(reader)
        self.writer = self.StreamWriter(writer)

    def get_reader(self) -> "StreamWrapper.StreamReader":
        return self.reader

    def get_reader_call(self):
        """
        Retrieves the callable function associated with the `recv` method of the
        reader attribute.

        Returns:
            Callable: A callable function that represents the `recv` method of
            the reader attribute.
        """
        return self.reader.recv

    def get_writer(self) -> "StreamWrapper.StreamWriter":
        return self.writer

    def get_writer_call(self):
        """
        Gets the callable method of the writer object to send data.

        The method serves as an interface to retrieve the `send` method
        from the `writer` attribute. It enables the calling of the writer’s
        `send` function for dispatching data.

        Returns:
            Callable: The `send` method of the writer object.
        """
        return self.writer.send

    async def close(self):
        """
        Close both the reader and writer streams.
        """
        await self.writer.close()
        self.reader.close()

    async def is_open(self) -> bool:
        """
        Check if the writer is still open.
        """
        return not await self.writer.is_closed()

    def get_sockname(self) -> Tuple[str, int]:
        """
        Get the socket name (address) of the writer.

        Returns:
            Tuple[str, int]: The socket name as a tuple (host, port). If the socket name is not available,
            returns ("", 0).
        """
        info = self.writer.get_sockname()
        return info if info else ("", 0)


class ClientConnection:
    """
    Represents a client connection managing multiple streams.

    This class provides functionality to manage streams associated
    with a client connection. Streams can be added, retrieved, or
    checked for availability based on their type. Additionally, this
    class handles determining if the connection is still open and
    ensures proper cleanup of streams when necessary. Intended for
    use in asynchronous network-based applications.

    Attributes:
        client_addr (Tuple[str, int]): The address of the client in
            the form of a tuple containing the host and port.
        wrappers (Dict[int, StreamWrapper]): A dictionary mapping
            stream types to their associated StreamWrapper instances.
        _is_closed (bool): Indicates whether the client connection
            has been closed.
    """

    def __init__(self, client_addr: Tuple[str, int]):
        self.client_addr = client_addr
        self.wrappers: Dict[int, StreamWrapper] = {}
        self._is_closed = False

    def add_stream(
        self,
        stream_type: int,
        reader: Optional[asyncio.StreamReader] = None,
        writer: Optional[asyncio.StreamWriter] = None,
        stream: Optional[StreamWrapper] = None,
    ) -> None:
        """
        Add a stream of the given type. If a stream is already present, it will be replaced.

        Args:
            stream_type (int): The type of the stream
            reader (Optional[asyncio.StreamReader]): The StreamReader for the stream
            writer (Optional[asyncio.StreamWriter]): The StreamWriter for the stream
            stream (Optional[StreamWrapper]): The StreamWrapper for the stream

        Raises:
            ValueError: If neither stream nor both reader and writer are provided
        """
        if stream:
            self.wrappers[stream_type] = stream
        elif reader and writer:
            self.wrappers[stream_type] = StreamWrapper(reader, writer)
        else:
            raise ValueError("Either stream or both reader and writer must be provided")

    def get_reader(self, stream_type: int) -> Optional[StreamWrapper.StreamReader]:
        """
        Get reader for stream type

        Args:
            stream_type (int): The type of the stream

        Returns:
            Optional[StreamWrapper.StreamReader]: The StreamReader if exists, else None
        """
        st = self.wrappers.get(stream_type)
        return st.get_reader() if st else None

    def get_writer(self, stream_type: int) -> Optional[StreamWrapper.StreamWriter]:
        """
        Get writer for stream type

        Args:
            stream_type (int): The type of the stream

        Returns:
            Optional[StreamWrapper.StreamWriter]: The StreamWriter if exists, else None
        """
        st = self.wrappers.get(stream_type)
        return st.get_writer() if st and st.get_writer() else None

    def get_stream(self, stream_type: int) -> Optional[StreamWrapper]:
        """Get both reader and writer for stream type"""
        return self.wrappers.get(stream_type)

    def has_stream(self, stream_type: int) -> bool:
        """Check if stream type exists"""
        return stream_type in self.wrappers

    def get_available_stream_types(self) -> list[int]:
        """Get list of available stream types"""
        return list(self.wrappers.keys())

    async def is_open(self) -> bool:
        """
        Check if connection is still open by checking if any writer is not closing.

        Returns:
            True if at least one stream is open
        """
        # If no streams exist, return False
        if not self.wrappers:
            return False

        # Check if any writer is not closing
        for stream in self.wrappers.values():
            if await stream.is_open():
                return True

        return False

    # def close(self):
    #     """Close all streams"""
    #     for writer in self.writers.values():
    #         if writer and not writer.is_closing():
    #             writer.close()
    #
    #     for reader in self.readers.values():
    #         if reader:
    #             reader.feed_eof()

    async def wait_closed(self):
        """
        Closes all open streams and marks the connection as closed.

        This asynchronous method ensures that all stream wrappers are properly
        closed before marking the connection as closed. It collects the closing
        tasks for all active streams and waits for their completion. Once all
        tasks are finished or if there are no active streams, it clears the
        stream wrappers and updates the internal state to indicate that the
        connection is closed.

        Raises:
            Exception: If an error occurs while closing any of the streams, an
                exception is raised with additional context.
        """
        if self._is_closed:
            return

        try:
            tasks = []
            for stream in self.wrappers.values():
                tasks.append(stream.close())

            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

                self.wrappers.clear()

            self._is_closed = True
        except asyncio.CancelledError:
            pass
        except Exception as e:
            raise Exception(f"Error while closing connection -> {e}") from e
