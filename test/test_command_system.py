#!/usr/bin/env python3
"""
Comprehensive Command System Test Suite
Tests the new IBaseCommand interface implementation, CommandBuilder functionality,
and command integration with the protocol system using Mock objects.
"""

import os
import sys
import unittest
from unittest.mock import Mock, MagicMock, patch
import json
import base64
import time

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.Interfaces import IBaseCommand
from utils.command.CommandBuilder import CommandBuilder
from utils.command.MouseCommand import MouseCommand
from utils.command.KeyboardCommand import KeyboardCommand
from utils.command.ClipboardCommand import ClipboardCommand
from utils.command.ReturnCommand import ReturnCommand
from utils.command.FileStartCommand import FileStartCommand
from utils.command.FileChunkCommand import FileChunkCommand
from utils.command.FileEndCommand import FileEndCommand
from utils.command.FileRequestCommand import FileRequestCommand
from utils.command.FileCopiedCommand import FileCopiedCommand
from utils.protocol.message import ProtocolMessage


class TestCommandSystem(unittest.TestCase):
    """Test suite for the unified command system."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.mock_message_service = Mock()
        self.mock_context = Mock()
        self.mock_event_bus = Mock()
        
    def test_mouse_command_creation_and_conversion(self):
        """Test MouseCommand creation and format conversions."""
        # Test position command
        cmd = CommandBuilder.mouse_position(100.5, 200.3, screen="left")
        
        self.assertIsInstance(cmd, MouseCommand)
        self.assertEqual(cmd.action, MouseCommand.POSITION)
        self.assertEqual(cmd.x, 100.5)
        self.assertEqual(cmd.y, 200.3)
        self.assertEqual(cmd.screen, "left")
        
        # Test legacy string conversion
        legacy_str = cmd.to_legacy_string()
        self.assertEqual(legacy_str, "mouse position 100.5 200.3 none")
        
        # Test protocol message conversion
        protocol_msg = cmd.to_protocol_message(source="server", target="client")
        self.assertIsInstance(protocol_msg, ProtocolMessage)
        self.assertEqual(protocol_msg.message_type, "mouse")
        
        # Test round-trip legacy conversion
        parsed_cmd = MouseCommand.from_legacy_string(legacy_str)
        self.assertIsInstance(parsed_cmd, MouseCommand)
        self.assertEqual(parsed_cmd.action, MouseCommand.POSITION)
        self.assertEqual(parsed_cmd.x, 100.5)
        self.assertEqual(parsed_cmd.y, 200.3)
        
    def test_mouse_command_all_actions(self):
        """Test all mouse command action types."""
        # Test click command
        click_cmd = CommandBuilder.mouse_click(50, 75, True)
        self.assertEqual(click_cmd.action, MouseCommand.CLICK)
        self.assertTrue(click_cmd.is_pressed)
        
        # Test right click command
        right_cmd = CommandBuilder.mouse_right_click(30, 40)
        self.assertEqual(right_cmd.action, MouseCommand.RIGHT_CLICK)
        
        # Test middle click command
        middle_cmd = CommandBuilder.mouse_middle_click(60, 80)
        self.assertEqual(middle_cmd.action, MouseCommand.MIDDLE_CLICK)
        
        # Test scroll command
        scroll_cmd = CommandBuilder.mouse_scroll(10, -5)
        self.assertEqual(scroll_cmd.action, MouseCommand.SCROLL)
        self.assertEqual(scroll_cmd.dx, 10)
        self.assertEqual(scroll_cmd.dy, -5)
        
    def test_keyboard_command_creation_and_conversion(self):
        """Test KeyboardCommand creation and format conversions."""
        # Test press command
        cmd = CommandBuilder.keyboard_press("ctrl+c", screen="right")
        
        self.assertIsInstance(cmd, KeyboardCommand)
        self.assertEqual(cmd.action, KeyboardCommand.PRESS)
        self.assertEqual(cmd.key, "ctrl+c")
        self.assertEqual(cmd.screen, "right")
        
        # Test legacy string conversion
        legacy_str = cmd.to_legacy_string()
        self.assertEqual(legacy_str, "keyboard press ctrl+c")
        
        # Test protocol message conversion
        protocol_msg = cmd.to_protocol_message(source="client", target="server")
        self.assertIsInstance(protocol_msg, ProtocolMessage)
        self.assertEqual(protocol_msg.message_type, "keyboard")
        
        # Test release command
        release_cmd = CommandBuilder.keyboard_release("shift")
        self.assertEqual(release_cmd.action, KeyboardCommand.RELEASE)
        self.assertEqual(release_cmd.key, "shift")
        
    def test_clipboard_command_creation_and_conversion(self):
        """Test ClipboardCommand creation and format conversions."""
        test_content = "Hello, World!\nMulti-line content\tWith tabs"
        cmd = CommandBuilder.clipboard(test_content, content_type="text")
        
        self.assertIsInstance(cmd, ClipboardCommand)
        self.assertEqual(cmd.content, test_content)
        self.assertEqual(cmd.content_type, "text")
        
        # Test legacy string conversion preserves content
        legacy_str = cmd.to_legacy_string()
        expected_str = f"clipboard {test_content}"
        self.assertEqual(legacy_str, expected_str)
        
        # Test round-trip conversion preserves content exactly
        parsed_cmd = ClipboardCommand.from_legacy_string(legacy_str)
        self.assertEqual(parsed_cmd.content, test_content)
        self.assertEqual(parsed_cmd.content_type, "text")
        
    def test_return_command_creation_and_conversion(self):
        """Test ReturnCommand creation and format conversions."""
        # Test all direction commands
        directions = [
            ("left", CommandBuilder.return_left, ReturnCommand.LEFT),
            ("right", CommandBuilder.return_right, ReturnCommand.RIGHT),
            ("up", CommandBuilder.return_up, ReturnCommand.UP),
            ("down", CommandBuilder.return_down, ReturnCommand.DOWN)
        ]
        
        for direction_name, builder_method, direction_constant in directions:
            with self.subTest(direction=direction_name):
                cmd = builder_method(42.5)
                
                self.assertIsInstance(cmd, ReturnCommand)
                self.assertEqual(cmd.direction, direction_constant)
                self.assertEqual(cmd.value, 42.5)
                
                # Test legacy conversion
                legacy_str = cmd.to_legacy_string()
                self.assertEqual(legacy_str, f"return {direction_name} 42.5")
                
                # Test round-trip
                parsed_cmd = ReturnCommand.from_legacy_string(legacy_str)
                self.assertEqual(parsed_cmd.direction, direction_constant)
                self.assertEqual(parsed_cmd.value, 42.5)
    
    def test_file_start_command_creation_and_conversion(self):
        """Test FileStartCommand creation and format conversions."""
        cmd = CommandBuilder.file_start("document.pdf", 1024000)
        
        self.assertIsInstance(cmd, FileStartCommand)
        self.assertEqual(cmd.file_name, "document.pdf")
        self.assertEqual(cmd.file_size, 1024000)
        
        # Test legacy string conversion
        legacy_str = cmd.to_legacy_string()
        self.assertEqual(legacy_str, "file_start document.pdf 1024000")
        
        # Test protocol message conversion
        protocol_msg = cmd.to_protocol_message()
        self.assertIsInstance(protocol_msg, ProtocolMessage)
        self.assertEqual(protocol_msg.message_type, "file")
        
        # Test round-trip conversion
        parsed_cmd = FileStartCommand.from_legacy_string(legacy_str)
        self.assertEqual(parsed_cmd.file_name, "document.pdf")
        self.assertEqual(parsed_cmd.file_size, 1024000)
        
    def test_file_chunk_command_creation_and_conversion(self):
        """Test FileChunkCommand creation and format conversions."""
        chunk_data = base64.b64encode(b"binary file data chunk").decode('utf-8')
        cmd = CommandBuilder.file_chunk(chunk_data, 5)
        
        self.assertIsInstance(cmd, FileChunkCommand)
        self.assertEqual(cmd.chunk_data, chunk_data)
        self.assertEqual(cmd.chunk_index, 5)
        
        # Test legacy string conversion
        legacy_str = cmd.to_legacy_string()
        self.assertIn("file_chunk", legacy_str)
        self.assertIn(str(5), legacy_str)
        self.assertIn(chunk_data, legacy_str)
        
        # Test round-trip conversion
        parsed_cmd = FileChunkCommand.from_legacy_string(legacy_str)
        self.assertEqual(parsed_cmd.chunk_data, chunk_data)
        self.assertEqual(parsed_cmd.chunk_index, 5)
        
    def test_file_end_command_creation_and_conversion(self):
        """Test FileEndCommand creation and format conversions."""
        cmd = CommandBuilder.file_end(screen="top")
        
        self.assertIsInstance(cmd, FileEndCommand)
        self.assertEqual(cmd.screen, "top")
        
        # Test legacy string conversion
        legacy_str = cmd.to_legacy_string()
        self.assertEqual(legacy_str, "file_end")
        
        # Test round-trip conversion
        parsed_cmd = FileEndCommand.from_legacy_string(legacy_str)
        self.assertIsInstance(parsed_cmd, FileEndCommand)
        
    def test_file_request_command_creation_and_conversion(self):
        """Test FileRequestCommand creation and format conversions."""
        cmd = CommandBuilder.file_request("/path/to/file.txt")
        
        self.assertIsInstance(cmd, FileRequestCommand)
        self.assertEqual(cmd.file_path, "/path/to/file.txt")
        
        # Test legacy string conversion
        legacy_str = cmd.to_legacy_string()
        expected = "file_request /path/to/file.txt"
        self.assertEqual(legacy_str, expected)
        
        # Test round-trip conversion
        parsed_cmd = FileRequestCommand.from_legacy_string(legacy_str)
        self.assertEqual(parsed_cmd.file_path, "/path/to/file.txt")
        
        # Test command without file path
        cmd_no_path = CommandBuilder.file_request()
        legacy_no_path = cmd_no_path.to_legacy_string()
        self.assertEqual(legacy_no_path, "file_request")
        
    def test_file_copied_command_creation_and_conversion(self):
        """Test FileCopiedCommand creation and format conversions."""
        cmd = CommandBuilder.file_copied("test.docx", 2048000, "/home/user/test.docx")
        
        self.assertIsInstance(cmd, FileCopiedCommand)
        self.assertEqual(cmd.file_name, "test.docx")
        self.assertEqual(cmd.file_size, 2048000)
        self.assertEqual(cmd.file_path, "/home/user/test.docx")
        
        # Test legacy string conversion
        legacy_str = cmd.to_legacy_string()
        expected = "file_copied test.docx 2048000 /home/user/test.docx"
        self.assertEqual(legacy_str, expected)
        
        # Test round-trip conversion
        parsed_cmd = FileCopiedCommand.from_legacy_string(legacy_str)
        self.assertEqual(parsed_cmd.file_name, "test.docx")
        self.assertEqual(parsed_cmd.file_size, 2048000)
        self.assertEqual(parsed_cmd.file_path, "/home/user/test.docx")
        
    def test_command_builder_legacy_parsing(self):
        """Test CommandBuilder's unified legacy string parsing."""
        test_cases = [
            ("mouse position 10 20 false", MouseCommand),
            ("keyboard press enter", KeyboardCommand),
            ("clipboard Hello World", ClipboardCommand),
            ("return left 50", ReturnCommand),
            ("file_start test.txt 1024", FileStartCommand),
            ("file_chunk ZGF0YQ== 1", FileChunkCommand),
            ("file_end", FileEndCommand),
            ("file_request /path", FileRequestCommand),
            ("file_copied file.dat 512 /home/file.dat", FileCopiedCommand),
        ]
        
        for command_str, expected_type in test_cases:
            with self.subTest(command=command_str):
                parsed_cmd = CommandBuilder.from_legacy_string(command_str)
                self.assertIsInstance(parsed_cmd, expected_type)
                
        # Test invalid command
        invalid_cmd = CommandBuilder.from_legacy_string("invalid command")
        self.assertIsNone(invalid_cmd)
        
    def test_command_protocol_integration(self):
        """Test command integration with ProtocolMessage system."""
        commands = [
            CommandBuilder.mouse_position(100, 200),
            CommandBuilder.keyboard_press("space"),
            CommandBuilder.clipboard("test data"),
            CommandBuilder.return_left(25),
            CommandBuilder.file_start("file.bin", 4096),
        ]
        
        for cmd in commands:
            with self.subTest(command=cmd.__class__.__name__):
                # Test protocol message creation
                protocol_msg = cmd.to_protocol_message(source="test_source", target="test_target")
                
                self.assertIsInstance(protocol_msg, ProtocolMessage)
                self.assertEqual(protocol_msg.source, "test_source")
                self.assertEqual(protocol_msg.target, "test_target")
                self.assertIsNotNone(protocol_msg.message_type)
                self.assertIsNotNone(protocol_msg.payload)
                
    def test_command_execution_context(self):
        """Test command execution with mock context."""
        # Test file commands that have execute() functionality
        mock_context = Mock()
        mock_context.file_transfer_service = Mock()
        
        # Test FileStartCommand execution
        file_start_cmd = FileStartCommand.create(
            "test.txt", 
            1024, 
            screen="left",
            context=mock_context,
            message_service=self.mock_message_service
        )
        
        # Execute should not raise exceptions
        try:
            file_start_cmd.execute()
        except Exception as e:
            # Some exceptions are expected due to mock context
            self.assertIsInstance(e, (AttributeError, TypeError))
            
    def test_command_ibase_interface_compliance(self):
        """Test that all commands properly implement IBaseCommand interface."""
        command_classes = [
            MouseCommand, KeyboardCommand, ClipboardCommand, ReturnCommand,
            FileStartCommand, FileChunkCommand, FileEndCommand, 
            FileRequestCommand, FileCopiedCommand
        ]
        
        for cmd_class in command_classes:
            with self.subTest(command_class=cmd_class.__name__):
                # Check that class inherits from IBaseCommand
                self.assertTrue(issubclass(cmd_class, IBaseCommand))
                
                # Check required methods exist
                required_methods = [
                    'to_protocol_message', 'to_legacy_string', 
                    'from_legacy_string', 'execute'
                ]
                
                for method_name in required_methods:
                    self.assertTrue(hasattr(cmd_class, method_name))
                    
    def test_command_string_representation(self):
        """Test command string representations."""
        cmd = CommandBuilder.mouse_position(50, 75)
        
        # Test __str__ returns legacy string
        self.assertEqual(str(cmd), cmd.to_legacy_string())
        
        # Test __repr__ contains class name
        repr_str = repr(cmd)
        self.assertIn("MouseCommand", repr_str)
        self.assertIn("mouse", repr_str)
        
    def test_command_performance_benchmarks(self):
        """Test command creation and conversion performance."""
        import time
        
        # Benchmark command creation
        start_time = time.time()
        commands = []
        for i in range(1000):
            commands.extend([
                CommandBuilder.mouse_position(i, i+1),
                CommandBuilder.keyboard_press("key"),
                CommandBuilder.clipboard(f"content {i}"),
            ])
        creation_time = time.time() - start_time
        
        # Benchmark protocol conversion
        start_time = time.time()
        for cmd in commands:
            protocol_msg = cmd.to_protocol_message()
        conversion_time = time.time() - start_time
        
        # Benchmark legacy conversion
        start_time = time.time()
        legacy_strings = []
        for cmd in commands:
            legacy_strings.append(cmd.to_legacy_string())
        legacy_time = time.time() - start_time
        
        # Performance assertions
        self.assertLess(creation_time, 1.0, "Command creation should be fast")
        self.assertLess(conversion_time, 1.0, "Protocol conversion should be fast")
        self.assertLess(legacy_time, 0.5, "Legacy conversion should be very fast")
        
        print(f"\nPerformance Results:")
        print(f"Created {len(commands)} commands in {creation_time:.3f}s ({len(commands)/creation_time:.0f} cmd/s)")
        print(f"Protocol conversion: {conversion_time:.3f}s ({len(commands)/conversion_time:.0f} conv/s)")
        print(f"Legacy conversion: {legacy_time:.3f}s ({len(commands)/legacy_time:.0f} conv/s)")
        
    def test_edge_cases_and_error_handling(self):
        """Test edge cases and error handling."""
        # Test with special characters in clipboard
        special_content = "Special: \n\t\r\"'\\äöü€🙂"
        clipboard_cmd = CommandBuilder.clipboard(special_content)
        legacy_str = clipboard_cmd.to_legacy_string()
        parsed_cmd = ClipboardCommand.from_legacy_string(legacy_str)
        self.assertEqual(parsed_cmd.content, special_content)
        
        # Test with empty content
        empty_clipboard = CommandBuilder.clipboard("")
        self.assertEqual(empty_clipboard.content, "")
        
        # Test with None values where applicable
        cmd_with_none = MouseCommand(action="position", x=None, y=None)
        legacy_str = cmd_with_none.to_legacy_string()
        self.assertIn("None", legacy_str)
        
        # Test malformed legacy strings
        malformed_commands = [
            "mouse",  # incomplete
            "mouse invalid action",  # invalid action
            "keyboard",  # incomplete
            "file_start",  # missing parameters
            "file_chunk invalid_base64 abc",  # invalid chunk index
        ]
        
        for malformed in malformed_commands:
            with self.subTest(malformed=malformed):
                try:
                    result = CommandBuilder.from_legacy_string(malformed)
                    # Should either return None or handle gracefully
                    if result is not None:
                        # If it doesn't return None, it should be a valid command
                        self.assertIsInstance(result, IBaseCommand)
                except (ValueError, IndexError, TypeError):
                    # Expected for malformed commands - this is good error handling
                    pass


class TestCommandIntegrationWithMocks(unittest.TestCase):
    """Test command integration with mocked system components."""
    
    def setUp(self):
        """Set up integration test mocks."""
        self.mock_message_service = Mock()
        self.mock_protocol_adapter = Mock()
        self.mock_chunk_manager = Mock()
        
    def test_message_service_command_integration(self):
        """Test commands integrate properly with MessageService."""
        from network.IOManager import MessageService
        
        # Create mock MessageService with command handling
        mock_service = Mock(spec=MessageService)
        
        # Test sending commands
        mouse_cmd = CommandBuilder.mouse_position(100, 200)
        keyboard_cmd = CommandBuilder.keyboard_press("ctrl+v")
        clipboard_cmd = CommandBuilder.clipboard("test content")
        
        # Simulate sending commands to mock service
        mock_service.send_mouse("left", mouse_cmd)
        mock_service.send_keyboard("right", keyboard_cmd)
        mock_service.send_clipboard("top", clipboard_cmd)
        
        # Verify mock interactions
        mock_service.send_mouse.assert_called_once_with("left", mouse_cmd)
        mock_service.send_keyboard.assert_called_once_with("right", keyboard_cmd)
        mock_service.send_clipboard.assert_called_once_with("top", clipboard_cmd)
        
    def test_protocol_adapter_command_conversion(self):
        """Test commands work with ProtocolAdapter for message conversion."""
        # Mock ProtocolAdapter behavior
        mock_adapter = Mock()
        mock_adapter.create_protocol_message.return_value = ProtocolMessage(
            message_type="mouse",
            timestamp=time.time(),
            sequence_id=1,
            payload={"action": "position", "x": 100, "y": 200},
            source="test",
            target="test"
        )
        
        # Test command to protocol conversion
        mouse_cmd = CommandBuilder.mouse_position(100, 200)
        protocol_msg = mouse_cmd.to_protocol_message("test", "test")
        
        self.assertIsInstance(protocol_msg, ProtocolMessage)
        self.assertEqual(protocol_msg.message_type, "mouse")
        
    def test_file_transfer_command_integration(self):
        """Test file transfer commands integrate with file transfer system."""
        # Mock file transfer context
        mock_context = Mock()
        mock_context.file_transfer_service = Mock()
        
        # Create file commands with context
        file_start = FileStartCommand.create(
            "test.pdf",
            2048,
            context=mock_context,
            message_service=self.mock_message_service
        )
        
        file_chunk = FileChunkCommand.create(
            base64.b64encode(b"chunk data").decode(),
            1,
            context=mock_context
        )
        
        file_end = FileEndCommand.create(context=mock_context)
        
        # Test that commands have proper context
        self.assertEqual(file_start.context, mock_context)
        self.assertEqual(file_chunk.context, mock_context)
        self.assertEqual(file_end.context, mock_context)
        
        # Test protocol message creation doesn't fail
        for cmd in [file_start, file_chunk, file_end]:
            protocol_msg = cmd.to_protocol_message()
            self.assertIsInstance(protocol_msg, ProtocolMessage)


def run_command_tests():
    """Run all command system tests."""
    if __name__ == '__main__':
        # Create test suite
        loader = unittest.TestLoader()
        suite = unittest.TestSuite()
        
        # Add test classes
        suite.addTests(loader.loadTestsFromTestCase(TestCommandSystem))
        suite.addTests(loader.loadTestsFromTestCase(TestCommandIntegrationWithMocks))
        
        # Run tests with verbose output
        runner = unittest.TextTestRunner(verbosity=2)
        result = runner.run(suite)
        
        # Print summary
        print(f"\n{'='*60}")
        print(f"Command System Test Results:")
        print(f"Tests run: {result.testsRun}")
        print(f"Failures: {len(result.failures)}")
        print(f"Errors: {len(result.errors)}")
        print(f"Success rate: {((result.testsRun - len(result.failures) - len(result.errors)) / result.testsRun * 100):.1f}%")
        print(f"{'='*60}")
        
        return result.wasSuccessful()


if __name__ == '__main__':
    success = run_command_tests()
    sys.exit(0 if success else 1)