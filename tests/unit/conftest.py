import time
from typing import List, Tuple, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from config import ApplicationConfig, ClientConfig, ServerConfig
from event import (
    BusEvent,
    ActiveScreenChangedEvent,
    ClientConnectedEvent,
    ClientDisconnectedEvent,
    ClientActiveEvent,
    CommandEvent,
    CrossScreenCommandEvent,
    MouseEvent,
    KeyboardEvent,
    ClipboardEvent,
    ScreenEvent,
)
from event.bus import AsyncEventBus, EventBus
from network.protocol.message import MessageType, ProtocolMessage


# ============================================================================
# Asyncio Backend Configuration
# ============================================================================


@pytest.fixture(
    params=[
        pytest.param(("asyncio", {"use_uvloop": True}), id="asyncio+uvloop"),
        pytest.param(("asyncio", {"use_uvloop": False}), id="asyncio"),
    ]
)
def anyio_backend(request):
    return request.param


# ============================================================================
# Directory and Config Fixtures
# ============================================================================


@pytest.fixture
def temp_dir(tmp_path):
    """Provide a temporary directory for tests."""
    return tmp_path


@pytest.fixture
def test_config_dir():
    """Provide the path to the tests/unit/config directory with default config files."""
    import os

    # Get the directory where this conftest.py file is located
    current_dir = os.path.dirname(__file__)
    return os.path.join(current_dir, "config")


@pytest.fixture
def app_config(temp_dir) -> ApplicationConfig:
    """Provide test application config with temp directory."""
    config = ApplicationConfig()
    config.set_save_path(str(temp_dir))
    return config


@pytest.fixture
def app_config_with_test_files(test_config_dir) -> ApplicationConfig:
    """Provide application config pointing to tests/unit/config directory."""
    import os

    config = ApplicationConfig()
    # Set main_path to the parent of config dir
    config.set_save_path(os.path.dirname(test_config_dir))
    # Config files are in the 'config' subdirectory
    config.config_path = "config/"
    return config


@pytest.fixture
def server_config(app_config) -> "ServerConfig":
    """Provide a clean ServerConfig instance for testing."""
    from config import ServerConfig

    return ServerConfig(app_config)


@pytest.fixture
def server_config_with_test_files(app_config_with_test_files) -> "ServerConfig":
    """Provide ServerConfig pointing to tests/unit/config directory."""
    from config import ServerConfig

    return ServerConfig(app_config_with_test_files)


@pytest.fixture
def client_config(app_config) -> "ClientConfig":
    """Provide a clean ClientConfig instance for testing."""
    from config import ClientConfig

    return ClientConfig(app_config)


@pytest.fixture
def client_config_with_test_files(app_config_with_test_files) -> "ClientConfig":
    """Provide ClientConfig pointing to tests/unit/config directory."""
    from config import ClientConfig

    return ClientConfig(app_config_with_test_files)


# ============================================================================
# Event Bus Fixtures
# ============================================================================


@pytest.fixture
def event_bus() -> AsyncEventBus:
    """Provide a real AsyncEventBus instance for testing."""
    return AsyncEventBus()


@pytest.fixture
def mock_event_bus():
    """Provide a mocked EventBus for unit testing."""
    bus = AsyncMock(spec=EventBus)
    bus.dispatch = AsyncMock()
    bus.dispatch_nowait = MagicMock()
    bus.subscribe = MagicMock()
    bus.unsubscribe = MagicMock()
    return bus


# ============================================================================
# Event Data Fixtures
# ============================================================================


@pytest.fixture
def active_screen_changed_event():
    """Provide a sample ActiveScreenChangedEvent."""
    return ActiveScreenChangedEvent(
        active_screen="client1",
        source="server",
        position=(0.5, 0.3),
    )


@pytest.fixture
def client_connected_event():
    """Provide a sample ClientConnectedEvent."""
    return ClientConnectedEvent(
        client_screen="client1",
        streams=[0, 1, 4, 12],
    )


@pytest.fixture
def client_disconnected_event():
    """Provide a sample ClientDisconnectedEvent."""
    return ClientDisconnectedEvent(
        client_screen="client1",
        streams=[0, 1, 4, 12],
    )


@pytest.fixture
def client_active_event():
    """Provide a sample ClientActiveEvent."""
    return ClientActiveEvent(client_screen="client1")


@pytest.fixture
def command_event():
    """Provide a sample CommandEvent."""
    return CommandEvent(
        command=CommandEvent.CROSS_SCREEN,
        source="client1",
        target="server",
        params={"x": 0.5, "y": 0.3},
    )


@pytest.fixture
def cross_screen_command_event():
    """Provide a sample CrossScreenCommandEvent."""
    return CrossScreenCommandEvent(
        source="client1",
        target="server",
        x=0.5,
        y=0.3,
    )


@pytest.fixture
def mouse_event():
    """Provide a sample MouseEvent."""
    return MouseEvent(
        x=100,
        y=200,
        dx=5,
        dy=10,
        button=1,
        action=MouseEvent.MOVE_ACTION,
        is_presed=False,
    )


@pytest.fixture
def mouse_click_event():
    """Provide a sample mouse click event."""
    return MouseEvent(
        x=100,
        y=200,
        button=1,
        action=MouseEvent.CLICK_ACTION,
        is_presed=True,
    )


@pytest.fixture
def keyboard_event():
    """Provide a sample KeyboardEvent."""
    return KeyboardEvent(
        key="a",
        action=KeyboardEvent.PRESS_ACTION,
    )


@pytest.fixture
def keyboard_release_event():
    """Provide a sample keyboard release event."""
    return KeyboardEvent(
        key="a",
        action=KeyboardEvent.RELEASE_ACTION,
    )


@pytest.fixture
def clipboard_event():
    """Provide a sample ClipboardEvent."""
    return ClipboardEvent(
        content="Test clipboard content",
        content_type="text",
    )


@pytest.fixture
def screen_event():
    """Provide a sample ScreenEvent."""
    return ScreenEvent(data={"cursor_x": 100, "cursor_y": 200, "screen": "client1"})


# ============================================================================
# Protocol Message Fixtures
# ============================================================================


def create_protocol_message(
    message_type: str,
    source: str,
    target: str,
    payload: dict,
    timestamp: float = 0.0,
    sequence_id: int = 1,
) -> ProtocolMessage:
    """Helper to create a ProtocolMessage with default values."""
    if timestamp is None:
        timestamp = time.time()

    return ProtocolMessage(
        message_type=message_type,
        timestamp=timestamp,
        sequence_id=sequence_id,
        source=source,
        target=target,
        payload=payload,
    )


@pytest.fixture
def protocol_message_command():
    """Provide a sample command ProtocolMessage."""
    return create_protocol_message(
        message_type=MessageType.COMMAND,
        source="client1",
        target="server",
        payload={
            "command": CommandEvent.CROSS_SCREEN,
            "params": {"x": 0.5, "y": 0.3},
        },
    )


@pytest.fixture
def protocol_message_mouse():
    """Provide a sample mouse ProtocolMessage."""
    return create_protocol_message(
        message_type=MessageType.MOUSE,
        source="client1",
        target="server",
        payload={
            "x": 100,
            "y": 200,
            "dx": 5,
            "dy": 10,
            "button": 1,
            "event": "move",
            "is_pressed": False,
        },
    )


@pytest.fixture
def protocol_message_keyboard():
    """Provide a sample keyboard ProtocolMessage."""
    return create_protocol_message(
        message_type=MessageType.KEYBOARD,
        source="client1",
        target="server",
        payload={
            "key": "a",
            "event": "press",
        },
    )


@pytest.fixture
def protocol_message_clipboard():
    """Provide a sample clipboard ProtocolMessage."""
    return create_protocol_message(
        message_type=MessageType.CLIPBOARD,
        source="client1",
        target="server",
        payload={
            "content": "Test content",
            "content_type": "text",
        },
    )


# ============================================================================
# Event Tracking Fixtures
# ============================================================================


@pytest.fixture
def event_tracker():
    """
    Fixture to track events dispatched through the event bus.
    Returns a tuple of (events_list, tracking_callback).
    """
    events: List[Tuple[int, BusEvent | None]] = []

    async def track_event(event_type: int, data: Optional[BusEvent] = None, **kwargs):
        """Track dispatched events."""
        events.append((event_type, data))

    return events, track_event


@pytest.fixture
def callback_tracker():
    """
    Fixture to track callback invocations.
    Returns a tuple of (call_count, call_args_list, callback).
    """
    call_count = {"count": 0}
    call_args_list = []

    async def tracked_callback(*args, **kwargs):
        """Callback that tracks its invocations."""
        call_count["count"] += 1
        call_args_list.append((args, kwargs))

    return call_count, call_args_list, tracked_callback
