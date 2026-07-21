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
"""Keyboard capture (server) -> injection (client) across the bridge."""

import pytest

from event import BusEventType, KeyboardEvent, ClientActiveEvent
from input.keyboard._base import KeyCode

from tests.integration.harness import build_bridge


@pytest.mark.anyio
async def test_server_keypress_injected_on_client():
    """A key press/release on the server is injected on the active client."""
    h = await build_bridge()
    try:
        # Activate the client so its keyboard controller accepts events.
        await h.client_bus.dispatch(
            event_type=BusEventType.CLIENT_ACTIVE,
            data=ClientActiveEvent(client_uid=h.client_uid),
        )
        await h.settle()

        kbd = h.client.kbd_mock

        # Server listener must be "listening" to forward keystrokes; that is
        # normally set by ACTIVE_SCREEN_CHANGED. Drive it directly here.
        h.server.kbd_listener._listening = True

        await h.server.kbd_listener.stream.send(
            KeyboardEvent(key="a", action=KeyboardEvent.PRESS_ACTION)
        )
        await h.wait_until(lambda: kbd.press.called)
        assert kbd.press.called, "expected the client to inject the key press"

        await h.server.kbd_listener.stream.send(
            KeyboardEvent(key="a", action=KeyboardEvent.RELEASE_ACTION)
        )
        await h.wait_until(lambda: kbd.release.called)
        assert kbd.release.called, "expected the client to inject the key release"
    finally:
        await h.stop()


@pytest.mark.anyio
async def test_keyboard_not_forwarded_when_server_not_focused():
    """The server must not forward keystrokes while focused on itself.

    Gating lives on the capture side: ``on_press`` only emits onto the
    keyboard stream when ``_listening`` is set (an active screen is a
    client). With the server unfocused, nothing crosses the bridge.
    """
    h = await build_bridge()
    try:
        await h.client_bus.dispatch(
            event_type=BusEventType.CLIENT_ACTIVE,
            data=ClientActiveEvent(client_uid=h.client_uid),
        )
        await h.settle()
        kbd = h.client.kbd_mock

        # Server focused on itself -> capture must swallow the keystroke.
        h.server.kbd_listener._listening = False
        h.server.kbd_listener.on_press(KeyCode(char="a"))
        h.server.kbd_listener.on_release(KeyCode(char="a"))
        await h.settle(30)

        assert not kbd.press.called
        assert not kbd.release.called
    finally:
        await h.stop()
