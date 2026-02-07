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
from unittest.mock import AsyncMock, MagicMock

import pytest

from network.data.exchange import MessageExchange, MessageExchangeConfig
from network.protocol.message import MessageType, ProtocolMessage
from utils.metrics import MetricsCollector


@pytest.fixture
def mock_metrics_collector():
    collector = MagicMock(spec=MetricsCollector)
    metrics_mock = MagicMock()
    # Mock to_dict to return something valid
    metrics_mock.to_dict.return_value = {"bytes_sent": 0, "bytes_received": 0}
    collector.register_connection = AsyncMock(return_value=metrics_mock)
    return collector


@pytest.fixture
async def exchange(mock_metrics_collector):
    config = MessageExchangeConfig()
    ex = MessageExchange(
        conf=config, id="test_ex", metrics_collector=mock_metrics_collector
    )
    await ex.start()
    yield ex
    await ex.stop()


@pytest.mark.anyio
class TestMessageExchange:
    async def test_initialization_and_metrics(self, mock_metrics_collector):
        ex = MessageExchange(id="init_test", metrics_collector=mock_metrics_collector)
        assert ex._id == "init_test"

        await ex.start()
        mock_metrics_collector.register_connection.assert_called_with("init_test")

        metrics = await ex.get_metrics()
        assert metrics is not None

        await ex.stop()

    async def test_set_transport(self, exchange):
        send_cb = MagicMock()
        recv_cb = MagicMock()

        await exchange.set_transport(send_callback=send_cb, receive_callback=recv_cb)

        # Accessing private members for verification
        assert exchange._send_callbacks["default"] == send_cb
        assert exchange._receive_callbacks["default"] == recv_cb

    async def test_send_basic_message_types(self, exchange):
        send_cb = MagicMock()
        await exchange.set_transport(send_callback=send_cb)

        # Mouse
        await exchange.send_mouse_data(x=10, y=20, event="move", dx=0, dy=0)
        assert send_cb.call_count == 1
        msg = ProtocolMessage.from_bytes(send_cb.call_args[0][0])
        assert msg.message_type == MessageType.MOUSE

        send_cb.reset_mock()

        # Keyboard
        await exchange.send_keyboard_data(key="a", event="up")
        assert send_cb.call_count == 1
        msg = ProtocolMessage.from_bytes(send_cb.call_args[0][0])
        assert msg.message_type == MessageType.KEYBOARD

        send_cb.reset_mock()

        # Screen
        await exchange.send_screen_command(command="lock")
        assert send_cb.call_count == 1
        msg = ProtocolMessage.from_bytes(send_cb.call_args[0][0])
        assert msg.message_type == MessageType.SCREEN

    async def test_receive_and_dispatch(self, exchange):
        handler_mock = AsyncMock()
        exchange.register_handler(MessageType.CLIPBOARD, handler_mock)

        # Prepare a message
        msg = exchange.builder.create_clipboard_message(content="test copy")
        msg_bytes = msg.to_bytes()

        # Create a receiver mock that feeds this message once
        received_event = asyncio.Event()

        async def mock_recv(size_hint):
            if not received_event.is_set():
                received_event.set()
                return msg_bytes
            # Return nothing on subsequent calls to keep loop running but idle
            await asyncio.sleep(0.01)
            return None

        await exchange.set_transport(receive_callback=mock_recv)

        # Wait for handler execution
        # We need to wait a bit for the async loop to pick up and process
        # A proper wait condition is checking calls

        for _ in range(50):
            if handler_mock.call_count > 0:
                break
            await asyncio.sleep(0.01)

        handler_mock.assert_called_once()
        processed_msg = handler_mock.call_args[0][0]
        assert processed_msg.message_type == MessageType.CLIPBOARD
        assert processed_msg.payload["content"] == "test copy"
        # After processing, the message queue should be empty
        assert exchange._message_queue.empty()

    async def test_auto_chunking_send(self, mock_metrics_collector):
        # Force small chunk size
        config = MessageExchangeConfig(max_chunk_size=500, auto_chunk=True)
        ex = MessageExchange(conf=config, metrics_collector=mock_metrics_collector)
        await ex.start()

        send_cb = MagicMock()
        await ex.set_transport(send_callback=send_cb)

        # Large payload
        large_data = "d" * 1000
        await ex.send_clipboard_data(content=large_data)

        # Should result in multiple calls
        assert send_cb.call_count > 1

        await ex.stop()

    async def test_chunk_reassembly_receive(self, exchange):
        handler_mock = AsyncMock()
        exchange.register_handler(MessageType.FILE, handler_mock)

        # Create a large message and chunk it manually
        original_msg = exchange.builder.create_file_message(
            command="transfer", data={"filename": "test.txt", "content": "x" * 2000}
        )

        chunks = exchange.builder.create_chunked_message(
            original_msg, max_chunk_size=500
        )
        assert len(chunks) > 1

        # We need to feed these chunks to the receiver
        # We'll use a queue to store them and feed one by one
        chunk_queue = asyncio.Queue()
        for chunk in chunks:
            chunk_queue.put_nowait(chunk.to_bytes())

        async def mock_recv(size_hint):
            if chunk_queue.empty():
                await asyncio.sleep(0.01)
                return None
            return await chunk_queue.get()

        await exchange.set_transport(receive_callback=mock_recv)

        # Wait for reassembly and dispatch
        for _ in range(50):
            if handler_mock.call_count > 0:
                break
            await asyncio.sleep(0.05)

        handler_mock.assert_called_once()
        received_msg = handler_mock.call_args[0][0]

        assert received_msg.message_type == MessageType.FILE
        assert received_msg.payload["content"] == "x" * 2000

    async def test_multicast_support(self, exchange):
        # Reconfigure for multicast
        exchange.config.multicast = True

        send_cb1 = MagicMock()
        send_cb2 = MagicMock()

        await exchange.set_transport(send_callback=send_cb1, tr_id="client1")
        await exchange.set_transport(send_callback=send_cb2, tr_id="client2")

        # In multicast we don't explicitly set a target (will be handled by the exchange layer)
        await exchange.send_command_message(command="ping")

        assert send_cb1.call_count == 1
        assert send_cb2.call_count == 1

        # Msg sent to client1 should have target client1
        msg1 = ProtocolMessage.from_bytes(send_cb1.call_args[0][0])
        assert msg1.target == "client1"

        msg2 = ProtocolMessage.from_bytes(send_cb2.call_args[0][0])
        assert msg2.target == "client2"
