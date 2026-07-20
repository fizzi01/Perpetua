"""macOS cursor handler. The mouse listener owns cursor
visibility and delta capture natively on this platform."""


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
from typing import Optional

from event.bus import EventBus
from network.stream.handler import StreamHandler
from utils.logging import get_logger


class CursorHandlerWorker:
    """No-op replacement for the wx-based worker on macOS.

    Cursor hiding and relative-delta capture are owned natively by the
    ``ServerMouseListener`` in ``input/mouse/_darwin.py`` (Quartz/CoreGraphics),
    so there is no overlay window or dedicated process here anymore. This stub
    keeps the ``CursorHandlerWorker`` surface the daemon expects.
    """

    def __init__(
        self,
        event_bus: EventBus,
        stream: Optional[StreamHandler] = None,
        debug: bool = False,
        window_class=None,
    ):
        self.event_bus = event_bus
        self.stream = stream
        self._debug = debug
        self.window_class = window_class

        self._is_running = False
        self.process = None
        self._mouse_data_task = None
        self._logger = get_logger(self.__class__.__name__)

    async def start(self, wait_ready: bool = True, timeout: float = 1) -> bool:
        self._is_running = True
        await asyncio.sleep(0)
        return True

    async def stop(self, timeout: float = 2) -> None:
        self._is_running = False
        await asyncio.sleep(0)

    def is_alive(self) -> bool:
        return self._is_running

    async def enable_capture(self) -> None:
        await asyncio.sleep(0)

    async def disable_capture(self, x: int = -1, y: int = -1) -> None:
        await asyncio.sleep(0)

    async def send_command(self, command) -> None:
        await asyncio.sleep(0)

    async def get_result(self, timeout: float = 0.1):
        await asyncio.sleep(0)
        return None

    async def get_all_results(self, timeout: float = 0.1):
        await asyncio.sleep(0)
        return []

    async def close_handler(self) -> None:
        await asyncio.sleep(0)
