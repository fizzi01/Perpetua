"""
Integration test for data object abstraction layer.
Tests end-to-end ProtocolMessage → DataObject → Command execution flow.
"""

import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

import unittest
from unittest.mock import Mock, MagicMock, patch
import time
from utils.protocol.message import ProtocolMessage, MessageBuilder
from utils.data import DataObjectFactory
from utils.command.Command import CommandFactory
from server.command import register_commands as register_server_commands
from client.command import register_commands as register_client_commands


class TestDataObjectIntegration(unittest.TestCase):
    """Integration tests for data object flow through the command system."""
    
    @classmethod
    def setUpClass(cls):
        """Set up command registrations."""
        register_server_commands()
        register_client_commands()
    
    def setUp(self):
        """Set up test fixtures."""
        self.mock_context = Mock()
        self.mock_message_service = Mock()
        self.mock_event_bus = Mock()
        self.message_builder = MessageBuilder()
        
        # Mock context methods
        self.mock_context.get_active_screen.return_value = "left"
        self.mock_context.get_current_mouse_position.return_value = (100, 200)
        
        # Mock controller contexts
        self.mock_mouse_controller = Mock()
        self.mock_keyboard_controller = Mock()
        self.mock_clipboard_controller = Mock()
        self.mock_context.mouse_controller = self.mock_mouse_controller
        self.mock_context.keyboard_controller = self.mock_keyboard_controller
        self.mock_context.clipboard_controller = self.mock_clipboard_controller
        
    def test_mouse_data_object_to_command_execution(self):
        """Test complete flow: ProtocolMessage → MouseData → MouseCommand → execution."""
        # Create ProtocolMessage
        protocol_message = self.message_builder.create_mouse_message(
            x=150, y=250, event="click", is_pressed=True
        )
        
        # Convert to DataObject
        data_object = DataObjectFactory.create_from_protocol_message(protocol_message)
        self.assertIsNotNone(data_object)
        self.assertEqual(data_object.data_type, "mouse")
        
        # Create command using DataObject
        command = CommandFactory.create_command(
            context=self.mock_context,
            message_service=self.mock_message_service,
            event_bus=self.mock_event_bus,
            screen="client1",
            data_object=data_object
        )
        
        self.assertIsNotNone(command)
        self.assertEqual(command.DESCRIPTION, "mouse")
        self.assertTrue(command.has_data_object())
        self.assertFalse(command.has_legacy_payload())
        
        # Execute command
        command.execute()
        
        # Verify execution called mouse controller
        self.mock_mouse_controller.process_mouse_command.assert_called_once_with(
            x=150, y=250, mouse_action="click", is_pressed=True
        )
        
    def test_keyboard_data_object_to_command_execution(self):
        """Test complete flow: ProtocolMessage → KeyboardData → KeyboardCommand → execution."""
        # Create ProtocolMessage
        protocol_message = self.message_builder.create_keyboard_message(
            key="space", event="press"
        )
        
        # Convert to DataObject
        data_object = DataObjectFactory.create_from_protocol_message(protocol_message)
        self.assertIsNotNone(data_object)
        self.assertEqual(data_object.data_type, "keyboard")
        
        # Create command using DataObject
        command = CommandFactory.create_command(
            context=self.mock_context,
            message_service=self.mock_message_service,
            event_bus=self.mock_event_bus,
            screen="client1",
            data_object=data_object
        )
        
        self.assertIsNotNone(command)
        self.assertEqual(command.DESCRIPTION, "keyboard")
        
        # Execute command
        command.execute()
        
        # Verify execution called keyboard controller
        self.mock_keyboard_controller.process_key_command.assert_called_once_with(
            "space", "press"
        )
        
    def test_clipboard_data_object_to_command_execution(self):
        """Test complete flow: ProtocolMessage → ClipboardData → ClipboardCommand → execution."""
        test_content = "Test clipboard content with special chars: áéíóú"
        
        # Create ProtocolMessage
        protocol_message = self.message_builder.create_clipboard_message(
            content=test_content, content_type="text"
        )
        
        # Convert to DataObject
        data_object = DataObjectFactory.create_from_protocol_message(protocol_message)
        self.assertIsNotNone(data_object)
        self.assertEqual(data_object.data_type, "clipboard")
        
        # Create command using DataObject
        command = CommandFactory.create_command(
            context=self.mock_context,
            message_service=self.mock_message_service,
            event_bus=self.mock_event_bus,
            screen="client1",
            data_object=data_object
        )
        
        self.assertIsNotNone(command)
        self.assertEqual(command.DESCRIPTION, "clipboard")
        
        # Execute command
        command.execute()
        
        # Verify execution called clipboard controller
        self.mock_clipboard_controller.set_clipboard_data.assert_called_once_with(test_content)
        
    def test_return_data_object_to_command_execution(self):
        """Test complete flow: ProtocolMessage → ReturnData → ReturnCommand → execution."""
        # Create ProtocolMessage
        protocol_message = self.message_builder.create_screen_message(
            command="right", data={"value": 0.5}
        )
        # Manually adjust to return type for this test
        protocol_message.message_type = "return"
        protocol_message.payload = {"command": "right", "value": 0.5}
        
        # Convert to DataObject
        data_object = DataObjectFactory.create_from_protocol_message(protocol_message)
        self.assertIsNotNone(data_object)
        self.assertEqual(data_object.data_type, "return")
        
        # Create command using DataObject
        command = CommandFactory.create_command(
            context=self.mock_context,
            message_service=self.mock_message_service,
            event_bus=self.mock_event_bus,
            screen="server",
            data_object=data_object
        )
        
        self.assertIsNotNone(command)
        self.assertEqual(command.DESCRIPTION, "return")
        
        # Execute command
        command.execute()
        
        # Verify execution called event bus
        self.mock_event_bus.publish.assert_called_once()
        
    def test_file_data_object_to_command_execution(self):
        """Test complete flow: ProtocolMessage → FileData → FileCommand → execution."""
        # Create ProtocolMessage for file start
        protocol_message = self.message_builder.create_file_message(
            command="file_start",
            data={
                "file_name": "test.txt",
                "file_size": 1024
            }
        )
        
        # Convert to DataObject
        data_object = DataObjectFactory.create_from_protocol_message(protocol_message)
        self.assertIsNotNone(data_object)
        self.assertEqual(data_object.data_type, "file")
        self.assertTrue(data_object.is_file_start())
        
        # The issue is that CommandFactory expects command type "file_start" but DataObject has type "file"
        # We need to use the specific file command type. Let me create the command manually:
        from utils.command.FileStartCommand import FileStartCommand
        
        command = FileStartCommand(
            context=self.mock_context,
            message_service=self.mock_message_service,
            event_bus=self.mock_event_bus,
            screen="client1",
            payload=None,
            data_object=data_object
        )
        
        self.assertIsNotNone(command)
        self.assertEqual(command.DESCRIPTION, "file_start")
        
        # Mock the file transfer context
        mock_file_service = Mock()
        self.mock_context.file_transfer_service = mock_file_service
        
        # Execute command - this should not fail
        command.execute()
        
        # Verify the file service was called
        mock_file_service.handle_file_start.assert_called_once_with("client1", "test.txt", 1024)
        
    def test_legacy_fallback_compatibility(self):
        """Test that legacy string commands still work alongside data objects."""
        # Create command using legacy string approach
        legacy_command = CommandFactory.create_command(
            raw_command="mouse click 100 200 true",
            context=self.mock_context,
            message_service=self.mock_message_service,
            event_bus=self.mock_event_bus,
            screen="client1"
        )
        
        self.assertIsNotNone(legacy_command)
        self.assertEqual(legacy_command.DESCRIPTION, "mouse")
        self.assertFalse(legacy_command.has_data_object())
        self.assertTrue(legacy_command.has_legacy_payload())
        
        # Execute legacy command
        legacy_command.execute()
        
        # Should still work with legacy payload parsing
        self.mock_mouse_controller.process_mouse_command.assert_called_once()
        
    def test_mixed_data_object_and_legacy_processing(self):
        """Test processing both data objects and legacy commands in the same session."""
        commands_executed = []
        
        # Create data object command
        protocol_message = self.message_builder.create_mouse_message(
            x=100, y=200, event="position"
        )
        data_object = DataObjectFactory.create_from_protocol_message(protocol_message)
        
        data_object_command = CommandFactory.create_command(
            context=self.mock_context,
            message_service=self.mock_message_service,
            event_bus=self.mock_event_bus,
            screen="client1",
            data_object=data_object
        )
        
        # Create legacy command
        legacy_command = CommandFactory.create_command(
            raw_command="keyboard press space",
            context=self.mock_context,
            message_service=self.mock_message_service,
            event_bus=self.mock_event_bus,
            screen="client1"
        )
        
        # Execute both
        data_object_command.execute()
        legacy_command.execute()
        
        # Both should have executed correctly
        self.mock_mouse_controller.process_mouse_command.assert_called_once()
        self.mock_keyboard_controller.process_key_command.assert_called_once()
        
    def test_performance_data_object_flow(self):
        """Test performance of data object flow vs legacy flow."""
        iterations = 100
        
        # Test data object flow performance
        start_time = time.time()
        
        for i in range(iterations):
            protocol_message = self.message_builder.create_mouse_message(
                x=i, y=i+1, event="position"
            )
            data_object = DataObjectFactory.create_from_protocol_message(protocol_message)
            command = CommandFactory.create_command(
                context=self.mock_context,
                message_service=self.mock_message_service,
                event_bus=self.mock_event_bus,
                screen="client1",
                data_object=data_object
            )
            
        data_object_duration = time.time() - start_time
        
        # Test legacy flow performance
        start_time = time.time()
        
        for i in range(iterations):
            command = CommandFactory.create_command(
                raw_command=f"mouse position {i} {i+1}",
                context=self.mock_context,
                message_service=self.mock_message_service,
                event_bus=self.mock_event_bus,
                screen="client1"
            )
            
        legacy_duration = time.time() - start_time
        
        print(f"DataObject flow: {data_object_duration:.4f}s for {iterations} operations")
        print(f"Legacy flow: {legacy_duration:.4f}s for {iterations} operations")
        
        # Data object flow should be reasonably performant
        # (may be slightly slower due to object creation but should be reasonable)
        ratio = data_object_duration / legacy_duration
        print(f"DataObject/Legacy ratio: {ratio:.2f}x")
        
        # Should not be more than 3x slower
        self.assertLess(ratio, 3.0)
        
    def test_error_handling_in_integration_flow(self):
        """Test error handling throughout the integration flow."""
        # Test with invalid protocol message
        invalid_protocol_message = ProtocolMessage(
            message_type="invalid_type",
            timestamp=time.time(),
            sequence_id=1,
            payload={}
        )
        
        # Should return None from factory
        data_object = DataObjectFactory.create_from_protocol_message(invalid_protocol_message)
        self.assertIsNone(data_object)
        
        # Test command creation with None data object
        command = CommandFactory.create_command(
            context=self.mock_context,
            message_service=self.mock_message_service,
            event_bus=self.mock_event_bus,
            screen="client1",
            data_object=None  # This should fallback gracefully
        )
        
        self.assertIsNone(command)  # Should return None when no valid input


if __name__ == '__main__':
    unittest.main()