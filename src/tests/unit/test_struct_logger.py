"""
Unit tests for StructLogger.

Tests the structured logging functionality including:
- Logger initialization with different configurations
- Log level filtering per logger instance
- Context binding and unbinding
- Thread safety
- Multiple logger instances with different levels
"""


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

import io
import logging
import sys
import threading
from contextlib import redirect_stdout

import pytest
import structlog

from utils.logging import StructLogger, get_logger, BaseLogger


class TestStructLoggerInitialization:
    """Test StructLogger initialization and configuration"""

    def setup_method(self):
        """Reset StructLogger configuration before each test"""
        StructLogger._configured = False
        StructLogger._global_config = {"verbose": True, "level": -1}
        StructLogger._logger_levels = {}
        # Reset structlog configuration
        structlog.reset_defaults()

    def test_default_initialization(self):
        """Test default logger initialization"""
        logger = StructLogger()

        assert logger.logging_enabled is True
        assert logger.logger_name == "main_app"
        assert logger._logger is not None

    def test_initialization_with_name(self):
        """Test logger initialization with custom name"""
        logger = StructLogger(name="test.module")

        assert logger.logger_name == "test.module"
        assert logger.logging_enabled is True

    def test_initialization_with_main_name(self):
        """Test logger initialization with __main__ name"""
        logger = StructLogger(name="__main__")

        assert logger.logger_name == "main_app"

    def test_initialization_verbose_false(self):
        """Test logger initialization with verbose=False"""
        logger = StructLogger(verbose=False)

        assert logger.logging_enabled is False
        assert StructLogger._global_config["verbose"] is False

    def test_initialization_with_level(self):
        """Test logger initialization with specific level"""
        StructLogger(level=BaseLogger.ERROR, is_root=True)

        assert StructLogger._global_config["level"] == logging.ERROR
        assert StructLogger._logger_levels.get("main_app") == logging.ERROR

    def test_initialization_with_context(self):
        """Test logger initialization with initial context"""
        logger = StructLogger(user_id=123, session="abc")

        # Context should be bound to the logger
        assert logger._logger is not None

    def test_singleton_configuration(self):
        """Test that configuration is applied only once"""
        StructLogger(name="logger1", verbose=True, level=BaseLogger.DEBUG, is_root=True)
        StructLogger(
            name="logger2", verbose=False, level=BaseLogger.ERROR, is_root=True
        )

        # First logger configures globally
        assert StructLogger._configured is True
        # Second logger should not change global config (but can have its own level)
        assert StructLogger._global_config["verbose"] is False
        assert StructLogger._logger_levels["logger2"] == logging.ERROR

    def test_root_logger_reconfiguration(self):
        """Test that root logger can reconfigure"""
        StructLogger(name="logger1", verbose=True)
        StructLogger(name="logger2", verbose=False, is_root=True)

        # Root logger should update global config
        assert StructLogger._global_config["verbose"] is False


class TestStructLoggerLevels:
    """Test logging level functionality"""

    def setup_method(self):
        """Reset configuration before each test"""
        StructLogger._configured = False
        StructLogger._global_config = {"verbose": True, "level": -1}
        StructLogger._logger_levels = {}
        structlog.reset_defaults()

    def test_set_level(self):
        """Test setting log level for a specific logger"""
        logger = StructLogger(name="test.logger", level=BaseLogger.INFO)

        assert StructLogger._logger_levels["test.logger"] == logging.INFO

        logger.set_level(BaseLogger.ERROR)
        assert StructLogger._logger_levels["test.logger"] == logging.ERROR

    def test_multiple_loggers_different_levels(self):
        """Test multiple loggers with different levels"""
        StructLogger(name="logger1", level=BaseLogger.DEBUG, is_root=True)
        StructLogger(name="logger2", level=BaseLogger.ERROR, is_root=True)

        assert StructLogger._logger_levels["logger1"] == logging.DEBUG
        assert StructLogger._logger_levels["logger2"] == logging.ERROR

    def test_level_parsing(self):
        """Test level parsing from custom to logging levels"""
        assert StructLogger._parse_level(BaseLogger.DEBUG) == logging.DEBUG
        assert StructLogger._parse_level(BaseLogger.INFO) == logging.INFO
        assert StructLogger._parse_level(BaseLogger.WARNING) == logging.WARNING
        assert StructLogger._parse_level(BaseLogger.ERROR) == logging.ERROR
        assert StructLogger._parse_level(StructLogger.CRITICAL) == logging.CRITICAL
        assert (
            StructLogger._parse_level(99) == logging.INFO
        )  # Unknown level defaults to INFO

    def test_log_filtering_by_level(self):
        """Test that logs are filtered based on level"""
        # Capture output
        output = io.StringIO()

        with redirect_stdout(output):
            logger = StructLogger(
                name="test.filter", level=BaseLogger.ERROR, is_root=True
            )

            # These should not appear (below ERROR level)
            logger.debug("debug message")
            logger.info("info message")
            logger.warning("warning message")

            # This should appear
            logger.error("error message")

        output_str = output.getvalue()

        # Debug, info, warning should NOT be in output
        assert "debug message" not in output_str
        assert "info message" not in output_str
        assert "warning message" not in output_str

        # Error should be in output
        assert "error message" in output_str


class TestStructLoggerMethods:
    """Test logging methods"""

    def setup_method(self):
        """Reset configuration before each test"""
        StructLogger._configured = False
        StructLogger._global_config = {"verbose": True, "level": -1}
        StructLogger._logger_levels = {}
        structlog.reset_defaults()

    def test_debug_method(self):
        """Test debug logging method"""
        output = io.StringIO()

        with redirect_stdout(output):
            logger = StructLogger(level=BaseLogger.DEBUG, is_root=True)
            logger.debug("debug message", key="value")

        output_str = output.getvalue()
        assert "debug message" in output_str

    def test_info_method(self):
        """Test info logging method"""
        output = io.StringIO()

        with redirect_stdout(output):
            logger = StructLogger(level=BaseLogger.INFO, is_root=True)
            logger.info("info message", key="value")

        output_str = output.getvalue()
        assert "info message" in output_str

    def test_warning_method(self):
        """Test warning logging method"""
        output = io.StringIO()

        with redirect_stdout(output):
            logger = StructLogger(level=BaseLogger.WARNING, is_root=True)
            logger.warning("warning message", key="value")

        output_str = output.getvalue()
        assert "warning message" in output_str

    def test_error_method(self):
        """Test error logging method"""
        output = io.StringIO()

        with redirect_stdout(output):
            logger = StructLogger(level=BaseLogger.ERROR, is_root=True)
            logger.error("error message", key="value")

        output_str = output.getvalue()
        assert "error message" in output_str

    def test_critical_method(self):
        """Test critical logging method"""
        output = io.StringIO()

        with redirect_stdout(output):
            logger = StructLogger(level=StructLogger.CRITICAL, is_root=True)
            logger.critical("critical message", key="value")

        output_str = output.getvalue()
        assert "critical message" in output_str

    def test_log_method_with_levels(self):
        """Test generic log method with different levels"""
        output = io.StringIO()

        with redirect_stdout(output):
            logger = StructLogger(level=BaseLogger.DEBUG, is_root=True)

            logger.log("debug via log", level=BaseLogger.DEBUG)
            logger.log("info via log", level=BaseLogger.INFO)
            logger.log("warning via log", level=BaseLogger.WARNING)
            logger.log("error via log", level=BaseLogger.ERROR)
            logger.log("critical via log", level=StructLogger.CRITICAL)

        output_str = output.getvalue()
        assert "debug via log" in output_str
        assert "info via log" in output_str
        assert "warning via log" in output_str
        assert "error via log" in output_str
        assert "critical via log" in output_str


class TestStructLoggerContext:
    """Test context binding and unbinding"""

    def setup_method(self):
        """Reset configuration before each test"""
        StructLogger._configured = False
        StructLogger._global_config = {"verbose": True, "level": -1}
        StructLogger._logger_levels = {}
        structlog.reset_defaults()

    def test_bind_context(self):
        """Test binding context to logger"""
        logger = StructLogger(name="test.context")
        bound_logger = logger.bind(user_id=123, session="abc")

        # Should return new instance
        assert isinstance(bound_logger, StructLogger)
        assert bound_logger is not logger

        # Should maintain logger properties
        assert bound_logger.logger_name == logger.logger_name
        assert bound_logger.logging_enabled == logger.logging_enabled

    def test_unbind_context(self):
        """Test unbinding context from logger"""
        logger = StructLogger(name="test.context", user_id=123, session="abc")
        unbound_logger = logger.unbind("user_id")

        # Should return new instance
        assert isinstance(unbound_logger, StructLogger)
        assert unbound_logger is not logger

        # Should maintain logger properties
        assert unbound_logger.logger_name == logger.logger_name
        assert unbound_logger.logging_enabled == logger.logging_enabled

    def test_initial_context(self):
        """Test logger initialization with initial context"""
        output = io.StringIO()

        with redirect_stdout(output):
            logger = StructLogger(
                name="test.context",
                level=BaseLogger.INFO,
                is_root=True,
                user_id=123,
                session="abc",
            )
            logger.info("test message")

        output_str = output.getvalue()
        assert "test message" in output_str
        # Context should be in output (structlog includes bound context)
        assert "user_id" in output_str or "123" in output_str


class TestStructLoggerThreadSafety:
    """Test thread safety of StructLogger"""

    def setup_method(self):
        """Reset configuration before each test"""
        StructLogger._configured = False
        StructLogger._global_config = {"verbose": True, "level": -1}
        StructLogger._logger_levels = {}
        structlog.reset_defaults()

    def test_concurrent_initialization(self):
        """Test concurrent logger initialization is thread-safe"""
        results = []
        errors = []

        def create_logger(name):
            try:
                logger = StructLogger(name=name, level=BaseLogger.INFO)
                results.append(logger)
            except Exception as e:
                errors.append(e)

        threads = []
        for i in range(10):
            t = threading.Thread(target=create_logger, args=(f"logger{i}",))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        # All threads should succeed
        assert len(errors) == 0
        assert len(results) == 10

        # Configuration should be set
        assert StructLogger._configured is True

    def test_concurrent_set_level(self):
        """Test concurrent set_level calls are thread-safe"""
        logger = StructLogger(name="test.concurrent", is_root=True)
        errors = []

        def set_level_task(level):
            try:
                logger.set_level(level)
            except Exception as e:
                errors.append(e)

        threads = []
        levels = [
            BaseLogger.DEBUG,
            BaseLogger.INFO,
            BaseLogger.WARNING,
            BaseLogger.ERROR,
            StructLogger.CRITICAL,
        ]

        for level in levels:
            t = threading.Thread(target=set_level_task, args=(level,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        # All threads should succeed
        assert len(errors) == 0

        # Level should be set (to one of the values)
        assert logger.logger_name in StructLogger._logger_levels

    def test_concurrent_logging(self):
        """Test concurrent logging operations"""
        logger = StructLogger(
            name="test.concurrent.log", level=BaseLogger.INFO, is_root=True
        )
        errors = []
        log_count = [0]
        lock = threading.Lock()

        def log_task(message):
            try:
                logger.info(message)
                with lock:
                    log_count[0] += 1
            except Exception as e:
                errors.append(e)

        threads = []
        for i in range(20):
            t = threading.Thread(target=log_task, args=(f"message {i}",))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        # All threads should succeed
        assert len(errors) == 0
        assert log_count[0] == 20


class TestGetLoggerFunction:
    """Test get_logger factory function"""

    def setup_method(self):
        """Reset configuration before each test"""
        StructLogger._configured = False
        StructLogger._global_config = {"verbose": True, "level": -1}
        StructLogger._logger_levels = {}
        structlog.reset_defaults()

    def test_get_structured_logger(self):
        """Test getting a structured logger"""
        logger = get_logger(name="test.factory", structured=True)

        assert isinstance(logger, StructLogger)
        assert isinstance(logger, BaseLogger)

    def test_get_structured_logger_with_level(self):
        """Test getting a structured logger with specific level"""
        logger = get_logger(
            name="test.factory.level", structured=True, level=BaseLogger.ERROR
        )

        assert isinstance(logger, StructLogger)
        assert StructLogger._logger_levels.get("test.factory.level") == logging.ERROR

    def test_get_structured_logger_with_context(self):
        """Test getting a structured logger with initial context"""
        logger = get_logger(
            name="test.factory.context", structured=True, user_id=123, session="abc"
        )

        assert isinstance(logger, StructLogger)
        assert logger._logger is not None

    def test_get_root_logger(self):
        """Test getting a root logger"""
        logger = get_logger(
            name="test.root", structured=True, is_root=True, level=BaseLogger.WARNING
        )

        assert isinstance(logger, StructLogger)
        assert StructLogger._global_config["level"] == logging.WARNING


class TestStructLoggerPerLoggerLevels:
    """Test per-logger level configuration"""

    def setup_method(self):
        """Reset configuration before each test"""
        StructLogger._configured = False
        StructLogger._global_config = {"verbose": True, "level": -1}
        StructLogger._logger_levels = {}
        structlog.reset_defaults()

    def test_independent_logger_levels(self):
        """Test that different loggers can have independent levels"""
        # Create root logger with INFO level
        StructLogger(name="root", level=BaseLogger.INFO, is_root=True)

        # Create child loggers with different levels
        StructLogger(name="module.debug", level=BaseLogger.DEBUG, is_root=True)
        StructLogger(name="module.error", level=BaseLogger.ERROR, is_root=True)

        # Verify each logger has its own level
        assert StructLogger._logger_levels["root"] == logging.INFO
        assert StructLogger._logger_levels["module.debug"] == logging.DEBUG
        assert StructLogger._logger_levels["module.error"] == logging.ERROR

    def test_logger_level_filtering_independence(self):
        """Test that log filtering works independently per logger"""
        output = io.StringIO()

        with redirect_stdout(output):
            # Create loggers with different levels
            debug_logger = StructLogger(
                name="debug.logger", level=BaseLogger.DEBUG, is_root=True
            )
            error_logger = StructLogger(name="error.logger", level=BaseLogger.ERROR)

            # Both loggers log at different levels
            debug_logger.debug("debug from debug_logger")
            debug_logger.error("error from debug_logger")

            error_logger.debug("debug from error_logger")
            error_logger.error("error from error_logger")

        output_str = output.getvalue()

        # debug_logger should show both
        assert "debug from debug_logger" in output_str
        assert "error from debug_logger" in output_str

        # error_logger should only show error
        assert "debug from error_logger" not in output_str
        assert "error from error_logger" in output_str

    def test_set_level_after_creation(self):
        """Test changing level after logger creation"""
        # Cattura stdout PRIMA di creare il logger
        captured_output = io.StringIO()
        original_stdout = sys.stdout

        try:
            # Imposta lo stdout PRIMA della configurazione
            sys.stdout = captured_output

            logger = StructLogger(
                name="dynamic.level", level=BaseLogger.ERROR, is_root=True
            )

            # Log at different levels (only error should appear)
            logger.debug("debug before")
            logger.error("error before")

            # Leggi l'output catturato
            output1_str = captured_output.getvalue()

            # Before: only error
            assert "debug before" not in output1_str
            assert "error before" in output1_str

            # Pulisci per il secondo test
            captured_output.truncate(0)
            captured_output.seek(0)

            # Change level to DEBUG
            logger.set_level(BaseLogger.DEBUG)

            # Log again (both should appear now)
            logger.debug("debug after")
            logger.error("error after")

            output2_str = captured_output.getvalue()

            # After: both
            assert "debug after" in output2_str
            assert "error after" in output2_str

        finally:
            # Ripristina stdout originale
            sys.stdout = original_stdout


class TestStructLoggerEdgeCases:
    """Test edge cases and error conditions"""

    def setup_method(self):
        """Reset configuration before each test"""
        StructLogger._configured = False
        StructLogger._global_config = {"verbose": True, "level": -1}
        StructLogger._logger_levels = {}
        structlog.reset_defaults()

    def test_logger_with_none_name(self):
        """Test logger with None as name"""
        logger = StructLogger(name=None)
        assert logger.logger_name == "main_app"

    def test_logger_with_empty_name(self):
        """Test logger with empty string as name"""
        logger = StructLogger(name="")
        assert logger.logger_name == ""

    def test_log_with_invalid_level(self):
        """Test logging with invalid level defaults to info"""
        output = io.StringIO()

        with redirect_stdout(output):
            logger = StructLogger(level=BaseLogger.INFO, is_root=True)
            logger.log("message with invalid level", level=999)

        output_str = output.getvalue()
        # Should still log (defaults to info)
        assert "message with invalid level" in output_str

    def test_multiple_bind_calls(self):
        """Test multiple bind calls create chained context"""
        logger = StructLogger(name="test.chain")
        bound1 = logger.bind(key1="value1")
        bound2 = bound1.bind(key2="value2")

        # Each should be a different instance
        assert bound1 is not logger
        assert bound2 is not bound1
        assert bound2 is not logger

        # All should maintain the same name
        assert logger.logger_name == bound1.logger_name == bound2.logger_name

    def test_exception_method(self):
        """Test exception logging method"""
        output = io.StringIO()

        with redirect_stdout(output):
            logger = StructLogger(level=BaseLogger.ERROR, is_root=True)

            try:
                raise ValueError("Test exception")
            except ValueError:
                logger.exception("Caught exception")

        output_str = output.getvalue()
        assert "Caught exception" in output_str


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
