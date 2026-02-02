#  Perpetua - open-source and cross-platform KVM software.
#  Copyright (c) 2026 Federico Izzi.
#
#  This program is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program.  If not, see <https://www.gnu.org/licenses/>.
#

import asyncio
from typing import Optional, Callable

from model.client import ClientObj


class CallbackError(Exception):
    """Custom exception for callback invocation errors."""

    pass


class BaseConnectionHandler:
    """
    Base class for connection handlers.
    """

    @staticmethod
    async def _invoke_callback(
        callback: Optional[Callable],
        client: Optional["ClientObj"],
        **kwargs,
    ):
        """
        Invokes a provided callback function with the given client and streams. If the callback
        is a coroutine function, it will be awaited; otherwise, it will be executed
        synchronously.

        Args:
            callback (Optional[Callable]): A function to process the client and streams.
                Can be a coroutine or a standard function.
            client (ClientObj): The client object to pass to the callback.
            streams (list[int]): A list of stream identifiers to pass to the callback.

        Raises:
            CallbackError: If an exception occurs during the execution of the callback.
        """
        if not callback:
            return

        try:
            if asyncio.iscoroutinefunction(callback):
                await callback(client, **kwargs)
            else:
                callback(client, **kwargs)
        except Exception as e:
            raise CallbackError(f"{e}") from e

    async def handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ):
        """
        Handle a new connection.

        Args:
            reader (asyncio.StreamReader): The stream reader for the connection.
            writer (asyncio.StreamWriter): The stream writer for the connection.
        """
        raise NotImplementedError("handle_connection must be implemented by subclasses")
