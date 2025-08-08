"""
Comprehensive test suite for the data object abstraction layer.
Tests the new ProtocolMessage → DataObject → Command execution flow.
"""

import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

import unittest
from unittest.mock import Mock, MagicMock, patch
import time
from utils.protocol.message import ProtocolMessage
from utils.data import (
    DataObjectFactory, IDataObject, MouseData, KeyboardData,
    ClipboardData, FileData, ReturnData
)


class TestDataObjectAbstraction(unittest.TestCase):
    """Test suite for data object abstraction layer functionality."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.mock_context = Mock()
        self.mock_message_service = Mock()
        self.mock_event_bus = Mock()
        
    def test_mouse_data_creation_from_protocol_message(self):
        """Test creating MouseData from ProtocolMessage."""
        protocol_message = ProtocolMessage(
            message_type="mouse",
            timestamp=time.time(),
            sequence_id=1,
            payload={
                "x": 100.5,
                "y": 200.3,
                "event": "click",
                "is_pressed": True
            },
            source="client1",
            target="server"
        )
        
        mouse_data = DataObjectFactory.create_from_protocol_message(protocol_message)
        
        self.assertIsInstance(mouse_data, MouseData)
        self.assertEqual(mouse_data.x, 100.5)
        self.assertEqual(mouse_data.y, 200.3)
        self.assertEqual(mouse_data.event, "click")
        self.assertTrue(mouse_data.is_pressed)
        self.assertEqual(mouse_data.source, "client1")
        self.assertEqual(mouse_data.target, "server")
        self.assertTrue(mouse_data.validate())
        
    def test_keyboard_data_creation_from_protocol_message(self):
        """Test creating KeyboardData from ProtocolMessage."""
        protocol_message = ProtocolMessage(
            message_type="keyboard",
            timestamp=time.time(),
            sequence_id=2,
            payload={
                "key": "a",
                "event": "press"
            }
        )
        
        keyboard_data = DataObjectFactory.create_from_protocol_message(protocol_message)
        
        self.assertIsInstance(keyboard_data, KeyboardData)
        self.assertEqual(keyboard_data.key, "a")
        self.assertEqual(keyboard_data.event, "press")
        self.assertTrue(keyboard_data.validate())
        self.assertTrue(keyboard_data.is_press_event())
        self.assertFalse(keyboard_data.is_release_event())
        
    def test_clipboard_data_creation_from_protocol_message(self):
        """Test creating ClipboardData from ProtocolMessage."""
        test_content = "Hello, World!\nThis is a test clipboard content with special chars: áéíóú ñ"
        
        protocol_message = ProtocolMessage(
            message_type="clipboard",
            timestamp=time.time(),
            sequence_id=3,
            payload={
                "content": test_content,
                "content_type": "text"
            }
        )
        
        clipboard_data = DataObjectFactory.create_from_protocol_message(protocol_message)
        
        self.assertIsInstance(clipboard_data, ClipboardData)
        self.assertEqual(clipboard_data.content, test_content)
        self.assertEqual(clipboard_data.content_type, "text")
        self.assertTrue(clipboard_data.validate())
        self.assertTrue(clipboard_data.is_text())
        self.assertEqual(clipboard_data.get_content_length(), len(test_content))
        
    def test_file_data_creation_from_protocol_message(self):
        """Test creating FileData from ProtocolMessage."""
        protocol_message = ProtocolMessage(
            message_type="file",
            timestamp=time.time(),
            sequence_id=4,
            payload={
                "command": "file_start",
                "file_name": "test.txt",
                "file_size": 1024
            }
        )
        
        file_data = DataObjectFactory.create_from_protocol_message(protocol_message)
        
        self.assertIsInstance(file_data, FileData)
        self.assertEqual(file_data.command, "file_start")
        self.assertEqual(file_data.file_name, "test.txt")
        self.assertEqual(file_data.file_size, 1024)
        self.assertTrue(file_data.validate())
        self.assertTrue(file_data.is_file_start())
        
    def test_return_data_creation_from_protocol_message(self):
        """Test creating ReturnData from ProtocolMessage."""
        protocol_message = ProtocolMessage(
            message_type="return",
            timestamp=time.time(),
            sequence_id=5,
            payload={
                "command": "left",
                "value": 0.5
            }
        )
        
        return_data = DataObjectFactory.create_from_protocol_message(protocol_message)
        
        self.assertIsInstance(return_data, ReturnData)
        self.assertEqual(return_data.command, "left")
        self.assertEqual(return_data.value, 0.5)
        self.assertTrue(return_data.validate())
        self.assertTrue(return_data.is_left())
        self.assertTrue(return_data.is_horizontal())
        
    def test_data_object_factory_unsupported_type(self):
        """Test DataObjectFactory with unsupported message type."""
        protocol_message = ProtocolMessage(
            message_type="unsupported",
            timestamp=time.time(),
            sequence_id=6,
            payload={}
        )
        
        data_object = DataObjectFactory.create_from_protocol_message(protocol_message)
        self.assertIsNone(data_object)
        
    def test_data_object_factory_invalid_message(self):
        """Test DataObjectFactory with invalid message."""
        with self.assertRaises(TypeError):
            DataObjectFactory.create_from_protocol_message("not a protocol message")
            
    def test_data_object_validation_failures(self):
        """Test data object validation with invalid data."""
        # Invalid mouse data
        invalid_mouse = MouseData(x="invalid", y=100, event="click")
        self.assertFalse(invalid_mouse.validate())
        
        # Invalid keyboard data
        invalid_keyboard = KeyboardData(key="", event="invalid_event")
        self.assertFalse(invalid_keyboard.validate())
        
        # Invalid clipboard data
        invalid_clipboard = ClipboardData(content=123, content_type="invalid_type")
        self.assertFalse(invalid_clipboard.validate())
        
    def test_mouse_data_special_events(self):
        """Test MouseData special event detection."""
        # Scroll event
        scroll_data = MouseData(x=0, y=0, event="scroll", dx=5.0, dy=-3.0)
        self.assertTrue(scroll_data.is_scroll_event())
        self.assertFalse(scroll_data.is_click_event())
        self.assertTrue(scroll_data.validate())
        
        # Position event
        position_data = MouseData(x=100, y=200, event="position")
        self.assertTrue(position_data.is_position_event())
        self.assertFalse(position_data.is_scroll_event())
        
    def test_file_data_chunk_operations(self):
        """Test FileData chunk-related operations."""
        # File chunk data
        chunk_data = FileData(
            command="file_chunk",
            chunk_data="base64encodeddata==",
            chunk_index=2,
            total_chunks=5
        )
        
        self.assertTrue(chunk_data.is_file_chunk())
        self.assertEqual(chunk_data.get_chunk_progress(), 0.6)  # (2+1)/5
        self.assertTrue(chunk_data.validate())
        
    def test_data_object_to_dict_conversion(self):
        """Test data object conversion to dictionary."""
        mouse_data = MouseData(x=150, y=250, event="click", is_pressed=True)
        mouse_dict = mouse_data.to_dict()
        
        expected_keys = ["x", "y", "event", "is_pressed"]
        for key in expected_keys:
            self.assertIn(key, mouse_dict)
            
        self.assertEqual(mouse_dict["x"], 150)
        self.assertEqual(mouse_dict["y"], 250)
        
    def test_data_object_factory_direct_creation(self):
        """Test DataObjectFactory direct creation methods."""
        # Direct mouse data creation
        mouse_data = DataObjectFactory.create_mouse_data(
            x=100, y=200, event="click", is_pressed=True
        )
        self.assertIsInstance(mouse_data, MouseData)
        self.assertEqual(mouse_data.x, 100)
        
        # Direct keyboard data creation
        keyboard_data = DataObjectFactory.create_keyboard_data(
            key="space", event="press"
        )
        self.assertIsInstance(keyboard_data, KeyboardData)
        self.assertEqual(keyboard_data.key, "space")
        
    def test_supported_types_and_registration(self):
        """Test DataObjectFactory supported types and registration."""
        supported_types = DataObjectFactory.get_supported_types()
        expected_types = ["mouse", "keyboard", "clipboard", "file", "return"]
        
        for expected_type in expected_types:
            self.assertIn(expected_type, supported_types)
            self.assertTrue(DataObjectFactory.is_supported_type(expected_type))
            
        self.assertFalse(DataObjectFactory.is_supported_type("unknown"))
        
    def test_complex_file_transfer_scenario(self):
        """Test complex file transfer scenario with multiple data objects."""
        # File start
        start_msg = ProtocolMessage(
            message_type="file",
            timestamp=time.time(),
            sequence_id=1,
            payload={
                "command": "file_start",
                "file_name": "large_file.bin",
                "file_size": 10240
            }
        )
        
        start_data = DataObjectFactory.create_from_protocol_message(start_msg)
        self.assertTrue(start_data.is_file_start())
        self.assertEqual(start_data.file_name, "large_file.bin")
        
        # File chunk
        chunk_msg = ProtocolMessage(
            message_type="file",
            timestamp=time.time(),
            sequence_id=2,
            payload={
                "command": "file_chunk",
                "chunk_data": "YmluYXJ5IGRhdGE=",  # base64 encoded
                "chunk_index": 0,
                "total_chunks": 3
            }
        )
        
        chunk_data = DataObjectFactory.create_from_protocol_message(chunk_msg)
        self.assertTrue(chunk_data.is_file_chunk())
        self.assertEqual(chunk_data.get_chunk_progress(), 1/3)
        
        # File end
        end_msg = ProtocolMessage(
            message_type="file",
            timestamp=time.time(),
            sequence_id=3,
            payload={
                "command": "file_end"
            }
        )
        
        end_data = DataObjectFactory.create_from_protocol_message(end_msg)
        self.assertTrue(end_data.is_file_end())
        
    def test_performance_data_object_creation(self):
        """Test performance of data object creation."""
        start_time = time.time()
        iterations = 1000
        
        for i in range(iterations):
            protocol_message = ProtocolMessage(
                message_type="mouse",
                timestamp=time.time(),
                sequence_id=i,
                payload={
                    "x": i * 1.5,
                    "y": i * 2.0,
                    "event": "position"
                }
            )
            
            data_object = DataObjectFactory.create_from_protocol_message(protocol_message)
            self.assertIsInstance(data_object, MouseData)
            
        end_time = time.time()
        duration = end_time - start_time
        
        # Should be able to create more than 500 data objects per second
        objects_per_second = iterations / duration
        self.assertGreater(objects_per_second, 500)
        print(f"DataObject creation performance: {objects_per_second:.0f} objects/second")
        
    def test_data_object_error_handling(self):
        """Test error handling in data object creation and validation."""
        # Test with malformed payload
        malformed_msg = ProtocolMessage(
            message_type="mouse",
            timestamp=time.time(),
            sequence_id=1,
            payload={
                "x": "not_a_number",
                "y": None,
                "event": 123  # Should be string
            }
        )
        
        # Should return None due to error in DataObjectFactory
        mouse_data = DataObjectFactory.create_from_protocol_message(malformed_msg)
        self.assertIsNone(mouse_data)  # Factory should return None on error
        
        # Test direct creation with invalid data - this should not validate
        invalid_mouse = MouseData(x="invalid", y=100, event="click")
        self.assertFalse(invalid_mouse.validate())
        
    def test_data_object_inheritance_and_interface(self):
        """Test that all data objects properly implement IDataObject interface."""
        data_objects = [
            MouseData(x=0, y=0, event="position"),
            KeyboardData(key="a", event="press"),
            ClipboardData(content="test"),
            FileData(command="file_start"),
            ReturnData(command="left", value=1.0)
        ]
        
        for data_object in data_objects:
            # Test interface compliance
            self.assertIsInstance(data_object, IDataObject)
            self.assertTrue(hasattr(data_object, 'data_type'))
            self.assertTrue(hasattr(data_object, 'to_dict'))
            self.assertTrue(hasattr(data_object, 'validate'))
            
            # Test methods work
            self.assertIsInstance(data_object.data_type, str)
            self.assertIsInstance(data_object.to_dict(), dict)
            self.assertIsInstance(data_object.validate(), bool)


if __name__ == '__main__':
    unittest.main()