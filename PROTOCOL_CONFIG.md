# Protocol Configuration Example

The improved protocol is now available in PyContinuity with timestamp-based ordering for smoother mouse cursor movement.

## Configuration

### Server Side (IOManager)
The new protocol is enabled by default. To configure:

```python
# In server initialization
message_service = MessageService(
    message_sender=message_sender,
    mouse=True,
    keyboard=True, 
    clipboard=True,
    file=True
)

# Protocol is automatically enabled
# message_service.use_structured_protocol = True (default)

# To disable new protocol (legacy mode):
# message_service.use_structured_protocol = False
```

### Client Side (ServerHandler) 
The client automatically detects and handles both protocol formats:

```python
# In client connection
handler = ServerHandler(connection, command_processor)

# Ordered processing is automatically enabled for mouse events
# Default delay tolerance: 50ms for smooth cursor movement

# To adjust delay tolerance:
# handler.ordered_processor.ordered_queue.max_delay_tolerance = 0.03  # 30ms
```

## Features

### Structured Messages
- JSON-based message format with timestamps
- Backward compatible with existing string commands
- Automatic detection and conversion

### Mouse Event Ordering
- Chronological processing of mouse events
- Configurable delay tolerance (default: 50ms)
- Prevents out-of-order cursor jumps

### Performance
- 70,000+ messages/second processing capability
- Optimized for real-time mouse/keyboard input
- Minimal overhead for non-mouse events

## Message Format

### New Structured Format
```json
{
  "message_type": "mouse",
  "timestamp": 1234567890.123,
  "sequence_id": 42,
  "payload": {
    "x": 100.0,
    "y": 200.0,
    "event": "move",
    "is_pressed": false
  },
  "source": "client1",
  "target": "server"
}
```

### Legacy Format (still supported)
```
mouse::move::100::200::false
```

The protocol automatically detects format and converts as needed for full compatibility.