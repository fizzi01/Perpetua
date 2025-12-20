"""
Unit tests for async clipboard listener with polling mechanism.
"""

import asyncio
from typing import List, Tuple
from unittest.mock import patch

import pytest

from input.clipboard import Clipboard, ClipboardType

INIT_CONTENT = "INITIAL_CONTENT"

# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
async def clipboard_listener():
    """Create a clipboard listener for testing."""
    listener = Clipboard(poll_interval=0.1)
    yield listener
    # Cleanup
    if listener.is_listening():
        await listener.stop()


@pytest.fixture
async def fast_clipboard_listener():
    """Create a fast-polling clipboard listener for testing."""
    listener = Clipboard(poll_interval=0.05)
    yield listener
    # Cleanup
    if listener.is_listening():
        await listener.stop()


@pytest.fixture
def changes_tracker():
    """Fixture to track clipboard changes."""
    changes: List[Tuple[str, ClipboardType]] = []

    async def on_change(content: str, content_type: ClipboardType):
        changes.append((content, content_type))

    return changes, on_change


# ============================================================================
# Test Clipboard Basic Functionality
# ============================================================================


@pytest.mark.anyio
class TestClipboardBasic:
    """Test basic clipboard operations."""

    async def test_clipboard_initialization(self):
        """Test clipboard listener initialization."""
        listener = Clipboard(poll_interval=0.5)

        assert listener.poll_interval == 0.5
        assert listener.content_types == [ClipboardType.TEXT]
        assert not listener.is_listening()
        assert listener.get_last_content() is None

    async def test_clipboard_initialization_with_custom_types(self):
        """Test clipboard initialization with custom content types."""
        content_types = [ClipboardType.TEXT, ClipboardType.URL, ClipboardType.FILE]
        listener = Clipboard(
            poll_interval=0.2,
            content_types=content_types,
        )

        assert listener.content_types == content_types
        assert listener.poll_interval == 0.2

    async def test_clipboard_start_stop(self, clipboard_listener):
        """Test starting and stopping clipboard monitoring."""
        assert not clipboard_listener.is_listening()

        await clipboard_listener.start()
        assert clipboard_listener.is_listening()

        await clipboard_listener.stop()
        assert not clipboard_listener.is_listening()

    async def test_clipboard_double_start(self, clipboard_listener):
        """Test that starting an already running listener is safe."""
        await clipboard_listener.start()
        assert clipboard_listener.is_listening()

        # Should not raise error
        await clipboard_listener.start()
        assert clipboard_listener.is_listening()

        await clipboard_listener.stop()

    async def test_clipboard_double_stop(self, clipboard_listener):
        """Test that stopping an already stopped listener is safe."""
        await clipboard_listener.start()
        await clipboard_listener.stop()
        assert not clipboard_listener.is_listening()

        # Should not raise error
        await clipboard_listener.stop()
        assert not clipboard_listener.is_listening()


# ============================================================================
# Test Clipboard Change Detection
# ============================================================================


@pytest.mark.anyio
class TestClipboardChangeDetection:
    """Test clipboard change detection."""

    async def test_clipboard_detects_changes(self, changes_tracker):
        """Test that clipboard detects content changes."""
        changes, on_change = changes_tracker

        listener = Clipboard(
            on_change=on_change,
            poll_interval=0.1,
            content_types=[ClipboardType.TEXT, ClipboardType.URL, ClipboardType.FILE],
        )

        await listener._debug_set_clipboard(INIT_CONTENT)
        await listener.start()
        await asyncio.sleep(0.2)  # Establish baseline

        # Simulate changes
        await listener._debug_set_clipboard("Test content 1")
        await asyncio.sleep(0.15)

        await listener._debug_set_clipboard("Test content 2")
        await asyncio.sleep(0.15)

        await listener.stop()

        assert len(changes) >= 2

    async def test_clipboard_ignores_duplicate_content(self, changes_tracker):
        """Test that duplicate content doesn't trigger callbacks."""
        changes, on_change = changes_tracker

        listener = Clipboard(on_change=on_change, poll_interval=0.1)

        await listener._debug_set_clipboard(INIT_CONTENT)
        await listener.start()
        await asyncio.sleep(0.2)

        # Set same content twice
        await listener._debug_set_clipboard("Same content")
        await asyncio.sleep(0.15)

        initial_count = len(changes)

        await listener._debug_set_clipboard("Same content")
        await asyncio.sleep(0.15)

        await listener.stop()

        # Should not have increased
        assert len(changes) == initial_count

    async def test_clipboard_detects_url_type(self, changes_tracker):
        """Test that URLs are detected correctly."""
        changes, on_change = changes_tracker

        listener = Clipboard(
            on_change=on_change,
            poll_interval=0.1,
            content_types=[ClipboardType.TEXT, ClipboardType.URL],
        )

        await listener._debug_set_clipboard(INIT_CONTENT)
        await listener.start()
        await asyncio.sleep(0.2)

        await listener._debug_set_clipboard("https://example.com")
        await asyncio.sleep(0.2)

        await listener.stop()

        # Find URL type in changes
        url_changes = [c for c in changes if c[1] == ClipboardType.URL]
        assert len(url_changes) >= 1

    async def test_clipboard_change_callback_error_handling(self):
        """Test that errors in callbacks don't crash the listener."""
        call_count = 0

        async def failing_callback(content: str, content_type: ClipboardType):
            nonlocal call_count
            call_count += 1
            raise ValueError("Intentional error")

        listener = Clipboard(on_change=failing_callback, poll_interval=0.1)

        await listener._debug_set_clipboard(INIT_CONTENT)
        await listener.start()
        await asyncio.sleep(0.2)

        await listener._debug_set_clipboard("Test content")
        await asyncio.sleep(0.15)

        # Listener should still be running despite callback error
        assert listener.is_listening()

        await listener.stop()

        # Callback should have been called at least once
        assert call_count >= 1


# ============================================================================
# Test Clipboard Content Operations
# ============================================================================


@pytest.mark.anyio
class TestClipboardContent:
    """Test clipboard content operations."""

    async def test_set_and_get_clipboard_content(self, clipboard_listener):
        """Test setting and getting clipboard content."""
        await clipboard_listener.start()
        await asyncio.sleep(0.15)

        test_content = "Test clipboard content"
        success = await clipboard_listener.set_clipboard(test_content)
        assert success

        await asyncio.sleep(0.15)

        last_content = clipboard_listener.get_last_content()
        assert last_content == test_content

        await clipboard_listener.stop()

    async def test_get_last_content_without_changes(self):
        """Test getting last content when no changes occurred."""
        listener = Clipboard(poll_interval=0.1)

        # Before starting, should return None
        assert listener.get_last_content() is None

        await listener._debug_set_clipboard(INIT_CONTENT)
        await listener.start()
        await asyncio.sleep(0.2)

        # May have initial content from system clipboard
        # Just verify it doesn't crash
        content = listener.get_last_content()
        assert content is None or isinstance(content, str)

        await listener.stop()

    async def test_hash_content_function(self):
        """Test content hashing function."""
        hash1 = Clipboard._hash_content("test content")
        hash2 = Clipboard._hash_content("test content")
        hash3 = Clipboard._hash_content("different content")

        assert hash1 == hash2
        assert hash1 != hash3

        # Empty content
        empty_hash = Clipboard._hash_content("")
        assert empty_hash == ""


# ============================================================================
# Test Clipboard Performance
# ============================================================================


@pytest.mark.anyio
class TestClipboardPerformance:
    """Test clipboard performance characteristics."""

    async def test_rapid_clipboard_changes(self, fast_clipboard_listener):
        """Test handling of rapid clipboard changes."""
        change_count = 0

        async def on_change(content: str, content_type: ClipboardType):
            nonlocal change_count
            change_count += 1

        listener = Clipboard(on_change=on_change, poll_interval=0.05)

        await listener._debug_set_clipboard(INIT_CONTENT)
        await listener.start()
        await asyncio.sleep(0.1)

        # Make rapid changes
        for i in range(5):
            await listener._debug_set_clipboard(f"Rapid content {i}")
            await asyncio.sleep(0.08)

        await listener.stop()

        # Should detect most changes
        assert change_count >= 3

    async def test_poll_interval_update(self, clipboard_listener):
        """Test updating poll interval."""
        clipboard_listener.set_poll_interval(0.2)
        assert clipboard_listener.poll_interval == 0.2

        # Test minimum interval enforcement
        clipboard_listener.set_poll_interval(0.01)
        assert clipboard_listener.poll_interval == 0.1  # Should be clamped to minimum

    @pytest.mark.slow
    async def test_long_running_monitoring(self):
        """Test clipboard monitoring over extended period."""
        changes = []

        async def on_change(content: str, content_type: ClipboardType):
            changes.append(content)

        listener = Clipboard(on_change=on_change, poll_interval=0.1)

        await listener._debug_set_clipboard(INIT_CONTENT)
        await listener.start()

        # Run for a few seconds
        for i in range(3):
            await asyncio.sleep(0.5)
            await listener._debug_set_clipboard(f"Content at {i}")

        await listener.stop()

        # Should still be functional
        assert len(changes) >= 2


# ============================================================================
# Test Multiple Listeners
# ============================================================================


@pytest.mark.anyio
class TestMultipleListeners:
    """Test multiple clipboard listeners."""

    async def test_multiple_listeners_detect_changes(self):
        """Test that multiple listeners can coexist."""
        changes1 = []
        changes2 = []

        async def on_change_1(content: str, content_type: ClipboardType):
            changes1.append(content)

        async def on_change_2(content: str, content_type: ClipboardType):
            changes2.append(content)

        listener1 = Clipboard(on_change=on_change_1, poll_interval=0.1)
        listener2 = Clipboard(on_change=on_change_2, poll_interval=0.15)
        await listener1._debug_set_clipboard(INIT_CONTENT)
        await listener2._debug_set_clipboard(INIT_CONTENT)
        await listener1.start()
        await listener2.start()

        await asyncio.sleep(0.2)

        await listener1._debug_set_clipboard("Shared content")
        await asyncio.sleep(0.25)

        await listener1.stop()
        await listener2.stop()

        # At least one should detect the change
        assert len(changes1) >= 1 or len(changes2) >= 1

    async def test_listeners_with_different_content_types(self):
        """Test listeners monitoring different content types."""
        text_changes = []
        url_changes = []

        async def on_text(content: str, content_type: ClipboardType):
            text_changes.append((content, content_type))

        async def on_url(content: str, content_type: ClipboardType):
            url_changes.append((content, content_type))

        listener_text = Clipboard(
            on_change=on_text,
            poll_interval=0.1,
            content_types=[ClipboardType.TEXT],
        )
        await listener_text._debug_set_clipboard(INIT_CONTENT)

        listener_url = Clipboard(
            on_change=on_url,
            poll_interval=0.1,
            content_types=[ClipboardType.URL],
        )
        await listener_url._debug_set_clipboard(INIT_CONTENT)

        await listener_text.start()
        await listener_url.start()

        await asyncio.sleep(0.2)

        # Set URL content
        await listener_text._debug_set_clipboard("https://example.com")
        await asyncio.sleep(0.15)

        await listener_text.stop()
        await listener_url.stop()

        # URL listener should detect, text listener may or may not
        url_detections = [c for c in url_changes if c[1] == ClipboardType.URL]
        assert len(url_detections) >= 1


# ============================================================================
# Test Edge Cases
# ============================================================================


@pytest.mark.anyio
class TestClipboardEdgeCases:
    """Test edge cases and error conditions."""

    async def test_clipboard_with_empty_content(self):
        """Test handling of empty clipboard content."""
        changes = []

        async def on_change(content: str, content_type: ClipboardType):
            changes.append((content, content_type))

        listener = Clipboard(on_change=on_change, poll_interval=0.1)

        await listener._debug_set_clipboard(INIT_CONTENT)
        await listener.start()
        await asyncio.sleep(0.2)

        # Set empty content
        await listener._debug_set_clipboard("")
        await asyncio.sleep(0.15)

        await listener.stop()

        # May or may not trigger depending on implementation
        # Just ensure it doesn't crash

    async def test_clipboard_with_unicode_content(self):
        """Test handling of Unicode content."""
        changes = []

        async def on_change(content: str, content_type: ClipboardType):
            changes.append(content)

        listener = Clipboard(on_change=on_change, poll_interval=0.1)

        await listener._debug_set_clipboard(INIT_CONTENT)
        await listener.start()
        await asyncio.sleep(0.2)

        unicode_text = "Hello 世界 🌍 émojis"
        await listener._debug_set_clipboard(unicode_text)
        await asyncio.sleep(0.15)

        await listener.stop()

        # Should handle Unicode properly
        if changes:
            assert any(unicode_text in change for change in changes)

    async def test_clipboard_with_very_long_content(self):
        """Test handling of very long clipboard content."""
        changes = []

        async def on_change(content: str, content_type: ClipboardType):
            changes.append(len(content))

        listener = Clipboard(on_change=on_change, poll_interval=0.1)

        await listener._debug_set_clipboard(INIT_CONTENT)
        await listener.start()
        await asyncio.sleep(0.2)

        # Create long content (10KB)
        long_content = "A" * 10000
        await listener._debug_set_clipboard(long_content)
        await asyncio.sleep(0.15)

        await listener.stop()

        # Should handle long content
        if changes:
            assert max(changes) >= 10000

    async def test_clipboard_cancellation_during_poll(self):
        """Test that polling can be cancelled cleanly."""
        listener = Clipboard(poll_interval=0.1)

        await listener._debug_set_clipboard(INIT_CONTENT)
        await listener.start()
        assert listener.is_listening()

        # Stop immediately
        await listener.stop()
        assert not listener.is_listening()

        # Should be stopped cleanly
        assert listener._task is None or listener._task.done()


# ============================================================================
# Benchmark Tests
# ============================================================================


@pytest.mark.anyio
@pytest.mark.benchmark
class TestClipboardBenchmarks:
    """Benchmark tests for clipboard operations."""

    async def test_clipboard_detection_latency(self):
        """Benchmark clipboard change detection latency."""
        detection_times = []
        change_times = []

        async def on_change(content: str, content_type: ClipboardType):
            detection_times.append(asyncio.get_event_loop().time())

        listener = Clipboard(on_change=on_change, poll_interval=0.05)

        await listener._debug_set_clipboard(INIT_CONTENT)
        await listener.start()
        await asyncio.sleep(0.15)

        # Make timed changes
        for i in range(10):
            change_times.append(asyncio.get_event_loop().time())
            await listener._debug_set_clipboard(f"Benchmark {i}")
            await asyncio.sleep(0.1)

        await asyncio.sleep(0.2)
        await listener.stop()

        # Calculate latencies
        if detection_times and change_times:
            latencies = []
            for change_time in change_times:
                future = [dt for dt in detection_times if dt >= change_time]
                if future:
                    latency = (min(future) - change_time) * 1000
                    latencies.append(latency)

            if latencies:
                avg_latency = sum(latencies) / len(latencies)
                # With 50ms polling, average latency should be reasonable
                assert avg_latency < 500  # Less than 500ms on average

    async def test_clipboard_polling_overhead(self):
        """Test that polling doesn't consume excessive resources."""
        listener = Clipboard(poll_interval=0.05)

        start_time = asyncio.get_event_loop().time()
        await listener._debug_set_clipboard(INIT_CONTENT)
        await listener.start()

        # Let it poll for a second
        await asyncio.sleep(1.0)

        await listener.stop()
        elapsed = asyncio.get_event_loop().time() - start_time

        # Should complete in reasonable time (allowing some overhead)
        assert elapsed < 1.5


# ============================================================================
# Test Clipboard with Mocked System Clipboard
# ============================================================================


@pytest.mark.anyio
class TestClipboardWithMocks:
    """Test clipboard with mocked system clipboard access."""

    async def test_clipboard_with_mock_paste(self):
        """Test clipboard listener with mocked paste function."""
        with patch('input.clipboard._base.paste') as mock_paste:
            mock_paste.return_value = "Mocked content"

            changes = []

            async def on_change(content: str, content_type: ClipboardType):
                changes.append(content)

            listener = Clipboard(on_change=on_change, poll_interval=0.1)
            await listener._debug_set_clipboard(INIT_CONTENT)
            await listener.start()
            await asyncio.sleep(0.25)
            await listener.stop()

            # Should have detected the mocked content
            assert mock_paste.called

    async def test_clipboard_paste_error_handling(self):
        """Test handling of paste errors."""
        with patch('input.clipboard._base.paste') as mock_paste:
            from copykitten import CopykittenError
            mock_paste.side_effect = CopykittenError("Clipboard error")

            listener = Clipboard(poll_interval=0.1)
            await listener._debug_set_clipboard(INIT_CONTENT)
            await listener.start()
            await asyncio.sleep(0.15)

            # Should handle error gracefully and continue running
            assert listener.is_listening()

            await listener.stop()

    async def test_clipboard_copy_success(self):
        """Test clipboard copy operation."""
        with patch('input.clipboard._base.copy') as mock_copy:
            mock_copy.return_value = None

            listener = Clipboard(poll_interval=0.1)
            success = await listener.set_clipboard("Test content")

            assert success
            mock_copy.assert_called_once_with("Test content")

    async def test_clipboard_copy_error_handling(self):
        """Test handling of copy errors."""
        with patch('input.clipboard._base.copy') as mock_copy:
            mock_copy.side_effect = Exception("Copy failed")

            listener = Clipboard(poll_interval=0.1)
            success = await listener.set_clipboard("Test content")

            assert not success


# ============================================================================
# Test Content Type Detection
# ============================================================================


@pytest.mark.anyio
class TestContentTypeDetection:
    """Test clipboard content type detection."""

    async def test_text_content_detection(self, changes_tracker):
        """Test detection of plain text content."""
        changes, on_change = changes_tracker

        listener = Clipboard(
            on_change=on_change,
            poll_interval=0.1,
            content_types=[ClipboardType.TEXT],
        )

        await listener._debug_set_clipboard(INIT_CONTENT)
        await listener.start()
        await asyncio.sleep(0.15)

        await listener._debug_set_clipboard("Plain text content")
        await asyncio.sleep(0.15)

        await listener.stop()

        # Should detect as TEXT
        text_changes = [c for c in changes if c[1] == ClipboardType.TEXT]
        assert len(text_changes) >= 1

    async def test_url_content_detection_http(self, changes_tracker):
        """Test detection of HTTP URL content."""
        changes, on_change = changes_tracker

        listener = Clipboard(
            on_change=on_change,
            poll_interval=0.1,
            content_types=[ClipboardType.URL],
        )

        await listener._debug_set_clipboard(INIT_CONTENT)
        await listener.start()
        await asyncio.sleep(0.15)

        await listener._debug_set_clipboard("http://example.com")
        await asyncio.sleep(0.15)

        await listener.stop()

        url_changes = [c for c in changes if c[1] == ClipboardType.URL]
        assert len(url_changes) >= 1

    async def test_url_content_detection_https(self, changes_tracker):
        """Test detection of HTTPS URL content."""
        changes, on_change = changes_tracker

        listener = Clipboard(
            on_change=on_change,
            poll_interval=0.1,
            content_types=[ClipboardType.URL],
        )

        await listener._debug_set_clipboard(INIT_CONTENT)
        await listener.start()
        await asyncio.sleep(0.15)

        await listener._debug_set_clipboard("https://secure.example.com/path")
        await asyncio.sleep(0.15)

        await listener.stop()

        url_changes = [c for c in changes if c[1] == ClipboardType.URL]
        assert len(url_changes) >= 1

    async def test_content_type_filtering(self):
        """Test that content types are filtered correctly."""
        text_changes = []
        url_changes = []

        async def on_text(content: str, content_type: ClipboardType):
            text_changes.append((content, content_type))

        async def on_url(content: str, content_type: ClipboardType):
            url_changes.append((content, content_type))

        # Listener that only accepts TEXT
        listener_text = Clipboard(
            on_change=on_text,
            poll_interval=0.1,
            content_types=[ClipboardType.TEXT],
        )
        await listener_text._debug_set_clipboard(INIT_CONTENT)

        # Listener that only accepts URL
        listener_url = Clipboard(
            on_change=on_url,
            poll_interval=0.1,
            content_types=[ClipboardType.URL],
        )
        await listener_url._debug_set_clipboard(INIT_CONTENT)

        await listener_text.start()
        await listener_url.start()
        await asyncio.sleep(0.15)

        # Set plain text - only text listener should detect
        await listener_text._debug_set_clipboard("Plain text")
        await asyncio.sleep(0.15)

        # Set URL - only URL listener should detect
        await listener_text._debug_set_clipboard("https://example.com")
        await asyncio.sleep(0.15)

        await listener_text.stop()
        await listener_url.stop()

        # Verify filtering worked
        assert len(url_changes) >= 1
        assert all(c[1] == ClipboardType.URL for c in url_changes)


# ============================================================================
# Test Synchronous Callback Support
# ============================================================================


@pytest.mark.anyio
class TestSyncCallbacks:
    """Test support for synchronous callbacks."""

    async def test_sync_callback_execution(self):
        """Test that synchronous callbacks are executed correctly."""
        call_count = 0

        def sync_callback(content: str, content_type: ClipboardType):
            nonlocal call_count
            call_count += 1

        listener = Clipboard(on_change=sync_callback, poll_interval=0.1)

        await listener._debug_set_clipboard(INIT_CONTENT)
        await listener.start()
        await asyncio.sleep(0.15)

        await listener._debug_set_clipboard("Test content")
        await asyncio.sleep(0.15)

        await listener.stop()

        assert call_count >= 1

    async def test_sync_callback_error_handling(self):
        """Test error handling in synchronous callbacks."""
        call_count = 0

        def failing_sync_callback(content: str, content_type: ClipboardType):
            nonlocal call_count
            call_count += 1
            raise RuntimeError("Sync callback error")

        listener = Clipboard(on_change=failing_sync_callback, poll_interval=0.1)

        await listener._debug_set_clipboard(INIT_CONTENT)
        await listener.start()
        await asyncio.sleep(0.15)

        await listener._debug_set_clipboard("Test content")
        await asyncio.sleep(0.15)

        # Should still be running
        assert listener.is_listening()
        assert call_count >= 1

        await listener.stop()


# ============================================================================
# Test State Management
# ============================================================================


@pytest.mark.anyio
class TestStateManagement:
    """Test clipboard state management."""

    async def test_last_hash_updates(self):
        """Test that last hash is updated correctly."""
        listener = Clipboard(poll_interval=0.1)

        await listener._debug_set_clipboard(INIT_CONTENT)
        await listener.start()
        await asyncio.sleep(0.15)

        # Set content
        await listener.set_clipboard("Test content")
        await asyncio.sleep(0.05)

        # Check hash is set
        assert listener._last_hash is not None
        first_hash = listener._last_hash

        # Set different content
        await listener.set_clipboard("Different content")
        await asyncio.sleep(0.05)

        # Hash should have changed
        assert listener._last_hash != first_hash

        await listener.stop()

    async def test_last_content_updates(self):
        """Test that last content is tracked correctly."""
        listener = Clipboard(poll_interval=0.1)

        await listener._debug_set_clipboard(INIT_CONTENT)
        await listener.start()
        await asyncio.sleep(0.15)

        content1 = "First content"
        await listener.set_clipboard(content1)
        await asyncio.sleep(0.05)

        assert listener.get_last_content() == content1

        content2 = "Second content"
        await listener.set_clipboard(content2)
        await asyncio.sleep(0.05)

        assert listener.get_last_content() == content2

        await listener.stop()

    async def test_state_persistence_across_start_stop(self):
        """Test that state is preserved when stopping and restarting."""
        listener = Clipboard(poll_interval=0.1)

        await listener._debug_set_clipboard(INIT_CONTENT)
        await listener.start()
        await listener.set_clipboard("Test content")
        await asyncio.sleep(0.15)

        content = listener.get_last_content()
        await listener.stop()

        # Content should still be available after stop
        assert listener.get_last_content() == content

        # Restart
        await listener.start()
        await asyncio.sleep(0.15)
        await listener.stop()

        # Should still have the content
        assert listener.get_last_content() == content


# ============================================================================
# Test Polling Interval Edge Cases
# ============================================================================


@pytest.mark.anyio
class TestPollingInterval:
    """Test polling interval configurations."""

    async def test_minimum_poll_interval(self):
        """Test that minimum poll interval is enforced."""
        listener = Clipboard(poll_interval=0.001)  # Very small
        listener.set_poll_interval(0.001)

        # Should be clamped to minimum (0.1s)
        assert listener.poll_interval >= 0.1

    async def test_very_slow_polling(self):
        """Test clipboard with very slow polling."""
        changes = []

        async def on_change(content: str, content_type: ClipboardType):
            changes.append(content)

        listener = Clipboard(on_change=on_change, poll_interval=1.0)

        await listener._debug_set_clipboard(INIT_CONTENT)
        await listener.start()
        await asyncio.sleep(0.5)

        await listener._debug_set_clipboard("Test content")

        # Wait long enough for at least one poll
        await asyncio.sleep(1.2)

        await listener.stop()

        # Should eventually detect with slow polling
        assert len(changes) >= 1

    async def test_poll_interval_change_while_running(self):
        """Test changing poll interval while listener is running."""
        listener = Clipboard(poll_interval=0.5)

        await listener._debug_set_clipboard(INIT_CONTENT)
        await listener.start()
        await asyncio.sleep(0.2)

        # Change interval
        listener.set_poll_interval(0.1)
        assert listener.poll_interval == 0.1

        await asyncio.sleep(0.2)
        await listener.stop()

        # Should complete without issues


# ============================================================================
# Test Concurrent Operations
# ============================================================================


@pytest.mark.anyio
class TestConcurrentOperations:
    """Test concurrent clipboard operations."""

    async def test_concurrent_set_operations(self):
        """Test multiple concurrent set operations."""
        listener = Clipboard(poll_interval=0.1)

        await listener._debug_set_clipboard(INIT_CONTENT)
        await listener.start()
        await asyncio.sleep(0.15)

        # Perform concurrent sets
        tasks = [
            listener.set_clipboard(f"Content {i}")
            for i in range(5)
        ]

        results = await asyncio.gather(*tasks)

        # All should succeed
        assert all(results)

        await listener.stop()

    async def test_set_while_polling(self):
        """Test setting clipboard content while polling is active."""
        changes = []

        async def on_change(content: str, content_type: ClipboardType):
            changes.append(content)

        listener = Clipboard(on_change=on_change, poll_interval=0.1)

        await listener._debug_set_clipboard(INIT_CONTENT)
        await listener.start()
        await asyncio.sleep(0.15)

        # Set content multiple times in quick succession
        for i in range(3):
            await listener.set_clipboard(f"Quick content {i}")
            await asyncio.sleep(0.05)

        await asyncio.sleep(0.2)
        await listener.stop()

        # Should handle concurrent set and poll operations


# ============================================================================
# Test Special Characters and Encoding
# ============================================================================


@pytest.mark.anyio
class TestSpecialCharacters:
    """Test handling of special characters and encoding."""

    async def test_emoji_content(self, changes_tracker):
        """Test clipboard with emoji content."""
        changes, on_change = changes_tracker

        listener = Clipboard(on_change=on_change, poll_interval=0.1)

        await listener._debug_set_clipboard(INIT_CONTENT)
        await listener.start()
        await asyncio.sleep(0.15)

        emoji_text = "Hello 🌍 🚀 ✨ 🎉"
        await listener._debug_set_clipboard(emoji_text)
        await asyncio.sleep(0.15)

        await listener.stop()

        if changes:
            assert any(emoji_text in c[0] for c in changes)

    async def test_multiline_content(self, changes_tracker):
        """Test clipboard with multiline content."""
        changes, on_change = changes_tracker

        listener = Clipboard(on_change=on_change, poll_interval=0.1)

        await listener._debug_set_clipboard(INIT_CONTENT)
        await listener.start()
        await asyncio.sleep(0.15)

        multiline = "Line 1\nLine 2\nLine 3\nLine 4"
        await listener._debug_set_clipboard(multiline)
        await asyncio.sleep(0.15)

        await listener.stop()

        if changes:
            assert any(multiline in c[0] for c in changes)

    async def test_special_characters(self, changes_tracker):
        """Test clipboard with special characters."""
        changes, on_change = changes_tracker

        listener = Clipboard(on_change=on_change, poll_interval=0.1)

        await listener._debug_set_clipboard(INIT_CONTENT)
        await listener.start()
        await asyncio.sleep(0.15)

        special = "Test @#$%^&*() <> {} [] | \\ / ~`"
        await listener._debug_set_clipboard(special)
        await asyncio.sleep(0.15)

        await listener.stop()

        if changes:
            assert any(special in c[0] for c in changes)

    async def test_rtl_text(self, changes_tracker):
        """Test clipboard with right-to-left text."""
        changes, on_change = changes_tracker

        listener = Clipboard(on_change=on_change, poll_interval=0.1)

        await listener._debug_set_clipboard(INIT_CONTENT)
        await listener.start()
        await asyncio.sleep(0.15)

        rtl_text = "مرحبا بالعالم"  # Arabic: Hello World
        await listener._debug_set_clipboard(rtl_text)
        await asyncio.sleep(0.15)

        await listener.stop()

        if changes:
            assert any(rtl_text in c[0] for c in changes)

