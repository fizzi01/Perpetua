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
"""Clipboard sync across the server<->client bridge, both directions."""

import pytest

from input.clipboard._base import ClipboardType

from tests.integration.harness import build_bridge


@pytest.mark.anyio
async def test_server_clipboard_broadcast_to_client():
    """A server clipboard change is written into the client's clipboard."""
    h = await build_bridge()
    try:
        # The listener only emits while it considers itself listening.
        h.server.clip_listener._listening = True

        await h.server.clip_listener._on_clipboard_change("hello", ClipboardType.TEXT)
        await h.wait_until(lambda: h.client.clipboard.get_last_content() == "hello")

        assert h.client.clipboard.get_last_content() == "hello"
    finally:
        await h.stop()


@pytest.mark.anyio
async def test_client_clipboard_to_server():
    """A client clipboard change is written into the server's clipboard."""
    h = await build_bridge()
    try:
        h.client.clip_listener._listening = True

        await h.client.clip_listener._on_clipboard_change("world", ClipboardType.URL)
        await h.wait_until(lambda: h.server.clipboard.get_last_content() == "world")

        assert h.server.clipboard.get_last_content() == "world"
    finally:
        await h.stop()


@pytest.mark.anyio
async def test_clipboard_not_broadcast_when_not_listening():
    """No clipboard change is forwarded while the listener isn't listening."""
    h = await build_bridge()
    try:
        # Default state: no connected/active client -> _listening is False.
        assert h.server.clip_listener._listening is False

        await h.server.clip_listener._on_clipboard_change("secret", ClipboardType.TEXT)
        await h.settle(30)

        assert h.client.clipboard.get_last_content() is None
    finally:
        await h.stop()


@pytest.mark.anyio
async def test_clipboard_content_type_roundtrip():
    """URL and FILE clipboard payloads round-trip in both directions."""
    h = await build_bridge()
    try:
        h.server.clip_listener._listening = True
        h.client.clip_listener._listening = True

        # Server -> client, URL payload.
        await h.server.clip_listener._on_clipboard_change(
            "https://example.com", ClipboardType.URL
        )
        await h.wait_until(
            lambda: h.client.clipboard.get_last_content() == "https://example.com"
        )
        assert h.client.clipboard.get_last_content() == "https://example.com"

        # Client -> server, FILE payload (newline-joined path list).
        await h.client.clip_listener._on_clipboard_change(
            "/tmp/a.txt\n/tmp/b.txt", ClipboardType.FILE
        )
        await h.wait_until(
            lambda: h.server.clipboard.get_last_content() == "/tmp/a.txt\n/tmp/b.txt"
        )
        assert h.server.clipboard.get_last_content() == "/tmp/a.txt\n/tmp/b.txt"
    finally:
        await h.stop()
