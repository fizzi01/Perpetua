import logging
import sys
import threading
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import structlog


class ColoredFormatter(logging.Formatter):
    """Formatter con colori per output console"""

    COLORS = {
        "DEBUG": "\033[92m",  # Green
        "INFO": "\033[94m",  # Blue
        "WARNING": "\033[93m",  # Yellow
        "ERROR": "\033[91m",  # Red
        "CRITICAL": "\033[1;31m",  # Dark Bold Red
        "RESET": "\033[0m",
    }

    def format(self, record):
        # Format timestamp
        cur_time = datetime.fromtimestamp(record.created).strftime("%H:%M:%S.%f")[:-3]

        # Get color for level
        color = self.COLORS.get(record.levelname, "")
        reset = self.COLORS["RESET"]

        # Format message
        return f"{color}[{cur_time}][{record.levelname}][{record.name}] {record.getMessage()}{reset}"


class SilentFormatter(logging.Formatter):
    """Formatter per modalità silent (solo errori e info importanti)"""

    COLORS = {
        "INFO": "\033[94m",  # Blue
        "WARNING": "\033[93m",  # Yellow
        "ERROR": "\033[91m",  # Red
        "RESET": "\033[0m",
    }

    def format(self, record):
        # Show only ERROR, WARNING, INFO without timestamp
        if record.levelno >= logging.INFO:
            color = self.COLORS.get(record.levelname, "")
            reset = self.COLORS["RESET"]
            return f"{color}[{record.levelname}][{record.name}] {record.getMessage()}{reset}"
        return ""


class BaseLogger(ABC):
    DEBUG = 0
    INFO = 1
    WARNING = 2
    ERROR = 3
    CRITICAL = 4

    @classmethod
    def _parse_level(cls, level: int) -> int:
        """Convert custom level to logging module level"""
        if level == cls.DEBUG:
            return logging.DEBUG
        elif level == cls.INFO:
            return logging.INFO
        elif level == cls.WARNING:
            return logging.WARNING
        elif level == cls.ERROR:
            return logging.ERROR
        elif level == cls.CRITICAL:
            return logging.CRITICAL
        else:
            return logging.INFO

    @abstractmethod
    def log(self, message: str, level: int = 0, **kw: Any):
        pass

    @abstractmethod
    def debug(self, message: str, **kw: Any):
        pass

    @abstractmethod
    def info(self, message: str, **kw: Any):
        pass

    @abstractmethod
    def warning(self, message: str, **kw: Any):
        pass

    @abstractmethod
    def error(self, message: str, **kw: Any):
        pass

    @abstractmethod
    def critical(self, message: str, **kw: Any):
        pass

    @abstractmethod
    def exception(self, message: str, **kw: Any):
        pass

    @abstractmethod
    def set_level(self, level: int):
        pass


class Logger(BaseLogger):
    """
    Python logger wrapper for simple logging.
    Provides colored output in verbose mode and silent output otherwise.
    """

    _app_logger_configured = False
    _lock = threading.Lock()
    _app_namespace = "main_app"
    _shared_handler = None

    def __init__(
        self,
        name=None,
        verbose=True,
        level: Optional[int] = None,
        stdout=None,
        log_file: Optional[str] = None,
    ):
        """
        Inizializza un logger per un modulo specifico.

        Args:
            name: Nome del logger (tipicamente __name__ del modulo). Se None, use default app namespace
            verbose: Se True usa ColoredFormatter con DEBUG, altrimenti SilentFormatter con INFO
            level: Livello di logging iniziale (Logger.DEBUG, Logger.INFO, ecc.). Se None, usa DEBUG se verbose è True, altrimenti INFO
            stdout: Funzione di output custom (deprecato, mantenuto per compatibilità)
        """
        self.logging_enabled = verbose
        self.stdout = stdout or print

        # Crea il nome del logger nel namespace dell'applicazione
        if name is None or name == "__main__":
            self.logger_name = self._app_namespace
        elif name is not None and name.startswith(self._app_namespace):
            # Se già inizia con il namespace, usalo così com'è
            self.logger_name = name
        else:
            # Aggiungi il namespace dell'applicazione
            self.logger_name = f"{name}"

        self._logger = logging.getLogger(self.logger_name)

        # NON propagare al root logger di Python per isolare dalle librerie esterne
        self._logger.propagate = False

        # Configura il logger dell'applicazione una sola volta
        with self._lock:
            if not Logger._app_logger_configured:
                self._configure_app_logger(verbose)
                Logger._app_logger_configured = True
                # Disabilita i logger delle librerie esterne comuni
                # self._silence_external_loggers()

        # Aggiungi l'handler condiviso a questo logger se non ce l'ha già
        if not self._logger.handlers and Logger._shared_handler:
            self._logger.addHandler(Logger._shared_handler)

        # Imposta il livello per questo specifico logger
        if verbose:
            self._logger.setLevel(logging.DEBUG)
        else:
            self._logger.setLevel(logging.INFO)

        if level is not None:
            self.set_level(level)

    def _configure_app_logger(self, log=True):
        """Configura il logger dell'applicazione una sola volta"""
        # Crea handler condiviso per console
        handler = logging.StreamHandler(sys.stdout)

        # Imposta formatter in base alla modalità
        if log:
            formatter = ColoredFormatter()
            handler.setLevel(logging.DEBUG)
        else:
            formatter = SilentFormatter()
            handler.setLevel(logging.INFO)

        handler.setFormatter(formatter)

        # Salva l'handler condiviso
        Logger._shared_handler = handler

    @staticmethod
    def _silence_external_loggers():
        """Silenzia i logger delle librerie esterne per evitare spam"""
        # Lista di logger comuni da silenziare (mostra solo WARNING e superiori)
        external_loggers = [
            "asyncio",
            "urllib3",
            "requests",
            "matplotlib",
            "PIL",
            "paramiko",
            "cryptography",
            "aiohttp",
            "websockets",
        ]

        for logger_name in external_loggers:
            ext_logger = logging.getLogger(logger_name)
            ext_logger.setLevel(logging.WARNING)

    def set_level(self, level: int):
        """
        Imposta il livello di logging per questo logger.

        Args:
            level: Priority level (DEBUG=0, INFO=1, WARNING=2, ERROR=3, CRITICAL=4)
        """

        logging_level = self._parse_level(level)

        # Imposta il livello del logger
        self._logger.setLevel(logging_level)

        # Aggiorna anche l'handler condiviso se esiste
        if Logger._shared_handler:
            Logger._shared_handler.setLevel(logging_level)

    def log(self, message, level: int = 0, **kw: Any):
        """
        Log un messaggio con la priorità specificata.

        Args:
            message: Messaggio da loggare
            level: Priority level (DEBUG=0, INFO=1, ERROR=2, WARNING=3)
        """
        # Mappa custom priority ai livelli logging
        if level == self.DEBUG:
            self._logger.debug(message)
        elif level == self.INFO:
            self._logger.info(message)
        elif level == self.ERROR:
            self._logger.error(message)
        elif level == self.CRITICAL:
            self._logger.critical(message)
        elif level == self.WARNING:
            self._logger.warning(message)
        else:
            # Default a INFO per valori non riconosciuti
            self._logger.info(message)

    # Metodi di convenience per compatibilità
    def debug(self, message: str, **kw: Any):
        """Log a debug level"""
        self.log(message, self.DEBUG)

    def info(self, message: str, **kw: Any):
        """Log a info level"""
        self.log(message, self.INFO)

    def warning(self, message: str, **kw: Any):
        """Log a warning level"""
        self.log(message, self.WARNING)

    def error(self, message: str, **kw: Any):
        """Log a error level"""
        self.log(message, self.ERROR)

    def critical(self, message: str, **kw: Any):
        """Log a critical level"""
        self.log(message, self.CRITICAL)

    @classmethod
    def _parse_level(cls, level: int) -> int:
        return super()._parse_level(level)

    def exception(self, message: str, **kw: Any):
        pass


class StructLogger(BaseLogger):
    """
    Encapsulates a logging utility for structured and thread-safe logging.

    StructLogger is designed to integrate with the structlog library for structured, customizable,
    and context-aware logging.
    """

    _lock = threading.Lock()
    _app_namespace = "main_app"
    _log_file_path = None
    _log_file_handle = None
    _configured = False
    _global_config: dict[str, bool | int] = {
        "verbose": True,
        "level": -1,  # -1 indicates not set
    }
    _root_logger = None
    _logger_levels: dict[str, int] = {}

    level_map = {
        "debug": logging.DEBUG,
        "info": logging.INFO,
        "warning": logging.WARNING,
        "error": logging.ERROR,
        "exception": logging.CRITICAL,
        "critical": logging.CRITICAL,
    }

    color_map = {
        "debug": "\033[92m",  # Green
        "info": "\033[94m",  # Blue
        "warning": "\033[93m",  # Yellow
        "error": "\033[91m",  # Red
        "critical": "\033[1;4;31m",  # Critical underlined and bold
        "exception": "\033[1;4;31m",
    }

    def __init__(
        self,
        name: Optional[str] = None,
        verbose: bool = True,
        level: Optional[int] = None,
        is_root: bool = False,
        log_file: Optional[str] = None,
        **initial_context,
    ):
        """
        Initializes the logger instance with the specified properties. Configures the internal logging
        mechanism, including the logger name, verbosity, and optional initial context. Utilizes the
        structlog library for structured logging and ensures that configuration is thread-safe.

        Attributes:
        logging_enabled: Indicates whether verbose logging is enabled or not.
        logger_name: Stores the name of the logger for use within the application.
        _logger: The bound structlog logger instance for structured logging.

        Args:
            name: Optional[str]
                The name of the logger. If not provided or set to '__main__', defaults
                to the application namespace.
            verbose: bool
                Indicates whether verbose logging is enabled. Defaults to True.
            level: int
                The initial logging level (Logger.DEBUG, Logger.INFO, etc.). If not
                provided, defaults to DEBUG if verbose is True, otherwise INFO.
            **initial_context: Keyword arguments
                Initial key-value pairs to bind to the logger for structured logging.
        """
        self.logging_enabled = verbose

        # Setup log file if specified (only once)
        if StructLogger._log_file_path is None and log_file is not None:
            StructLogger._log_file_path = log_file
            # Open file for writing
            log_path = Path(log_file)
            log_path.parent.mkdir(parents=True, exist_ok=True)

            # Open the file handle directly for structlog
            StructLogger._log_file_handle = open(
                log_file, "a", encoding="utf-8", buffering=1
            )
        elif log_file is None and StructLogger._log_file_path is not None and is_root:
            StructLogger._log_file_path = None
            StructLogger._log_file_handle = None
        elif (
            log_file is not None and StructLogger._log_file_path != log_file and is_root
        ):
            # Close previous file handle if different log file is specified for root
            if StructLogger._log_file_handle is not None:
                StructLogger._log_file_handle.close()
            StructLogger._log_file_path = log_file
            log_path = Path(log_file)
            log_path.parent.mkdir(parents=True, exist_ok=True)

            StructLogger._log_file_handle = open(
                log_file, "a", encoding="utf-8", buffering=1
            )

        # Create the logger name within the application namespace
        if name is None or name == "__main__":
            self.logger_name = self._app_namespace
        else:
            self.logger_name = name

        if level is None:
            level = self.DEBUG if verbose else self.INFO

        # Store the requested level for this specific logger
        requested_level = level

        # Configure structlog in a thread-safe manner
        with self._lock:
            # Initialize _logger_levels if not exists
            if not hasattr(StructLogger, "_logger_levels"):
                StructLogger._logger_levels = {}

            # Register this logger's level BEFORE configuration
            StructLogger._logger_levels[self.logger_name] = self._parse_level(
                requested_level
            )

            if not StructLogger._configured or is_root:
                # First logger or root: configure structlog globally
                StructLogger._global_config["verbose"] = verbose
                StructLogger._global_config["level"] = self._parse_level(
                    level if is_root else self.DEBUG
                )
                StructLogger._root_logger = self.logger_name if is_root else ""
                self._configure_structlog(verbose, StructLogger._global_config["level"])
                StructLogger._configured = True
            # For non-root loggers, we don't reconfigure structlog
            # The filter_by_level processor will use _logger_levels for per-logger filtering

        # Get the bound logger
        self._logger = structlog.get_logger().bind(logger=self.logger_name)

        # Bind context
        if initial_context:
            self._logger = self._logger.bind(**initial_context)

    def _configure_structlog(self, verbose: bool = True, level: Optional[int] = None):
        """
        Configure structlog with processors and renderers based on verbosity.

        This method sets up structlog's configuration to handle structured logging
        with different processors and renderers depending on whether verbose logging
        is enabled or not. It defines common processors for both modes and then
        configures structlog accordingly.

        Args:
            verbose (bool): If True, configures structlog for verbose output with
                            color and detailed information. If False, configures for
                            silent output with minimal information.
        """

        def filter_by_level(logger, method_name, event_dict):
            """
            Filter log messages based on the configured logging level.

            This processor checks the logging level of each log message against the
            configured minimum logging level. If the message's level is lower than
            the configured level, it is filtered out (not logged).

            Args:
                logger: The logger instance.
                method_name: The name of the logging method (e.g., 'debug', 'info').
                event_dict: The dictionary containing log event data.

            Returns:
                dict: The original event_dict if the message should be logged.

            Raises:
                structlog.DropEvent: If the message should be filtered out.
            """
            # We capture the configured level for use in the filter processor
            configured_level = StructLogger._global_config.get("level")

            if configured_level is None:
                raise structlog.DropEvent

            # Get the logger name from event_dict
            logger_name = event_dict.get("logger", "")

            # We get as min_levlel the max between the global configured level and the specific logger level
            # We do so to ensure that the global level (root) acts as a floor for all loggers
            min_level = max(
                StructLogger._logger_levels.get(logger_name, configured_level),
                configured_level,
            )

            if min_level is None:
                return event_dict

            message_level = StructLogger.level_map.get(method_name, logging.INFO)

            if message_level >= min_level:
                return event_dict
            else:
                # Use DropEvent to properly stop the processor chain
                raise structlog.DropEvent

        shared_processors = [
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            filter_by_level,
            structlog.processors.TimeStamper(fmt="[%H:%M:%S.%f]", utc=False),
            structlog.processors.StackInfoRenderer(),
        ]

        # Use BoundLogger without filtering wrapper
        # All filtering is handled by filter_by_level processor
        wrapper_cls = structlog.BoundLogger

        # Determine output file: use log file if opened, otherwise stdout
        output_file = (
            StructLogger._log_file_handle
            if StructLogger._log_file_handle is not None
            else sys.stdout
        )

        if verbose:
            # Verbose mode: colored output with all details
            # Use colors only for stdout, not for file
            use_colors = output_file == sys.stdout
            structlog.configure(
                processors=shared_processors
                + [
                    structlog.processors.format_exc_info,
                    structlog.dev.ConsoleRenderer(
                        colors=use_colors,
                        pad_event_to=40,
                        level_styles=self.color_map if use_colors else None,
                    ),
                ],
                wrapper_class=wrapper_cls,  # type: ignore
                logger_factory=structlog.WriteLoggerFactory(file=output_file),
                cache_logger_on_first_use=False,
            )
        else:
            # Silent mode: minimal output without colors
            structlog.configure(
                processors=shared_processors
                + [
                    structlog.processors.format_exc_info,
                    structlog.dev.ConsoleRenderer(
                        colors=False,
                        pad_event_to=40,
                    ),
                ],
                wrapper_class=wrapper_cls,  # type: ignore
                logger_factory=structlog.WriteLoggerFactory(file=output_file),
                cache_logger_on_first_use=False,
            )

    def bind(self, **context: Any) -> "StructLogger":
        """
        Creates a new instance of the StructLogger class with bound context.

        This method generates a new instance of the StructLogger class and binds the
        provided context to the logger. The existing logging configuration, such as
        the logging status and logger name, will be inherited by the new instance.

        Parameters:
            context (Any): Key-value pairs to bind as context to the logger.

        Returns:
            StructLogger: A new instance of StructLogger with the given context bound.
        """
        new_instance = StructLogger.__new__(StructLogger)
        new_instance.logging_enabled = self.logging_enabled
        new_instance.logger_name = self.logger_name
        new_instance._logger = self._logger.bind(**context)
        return new_instance

    def unbind(self, *keys: str) -> "StructLogger":
        """
        Unbind one or more keys from the logger's structured context.

        This method returns a new logger instance derived from the current one, but with
        the specified keys removed from the structured context. It does not modify the
        state of the original logger instance.

        Parameters:
            keys: str
                One or more keys to be unbound from the logger's structured context.

        Returns:
            StructLogger
                A new instance of StructLogger with the specified keys removed from its
                structured context.
        """
        new_instance = StructLogger.__new__(StructLogger)
        new_instance.logging_enabled = self.logging_enabled
        new_instance.logger_name = self.logger_name
        new_instance._logger = self._logger.unbind(*keys)
        return new_instance

    def log(self, message: str, level: int = 0, **kw: Any):
        """

        Logs a message with the specified logging level. The method routes the message to the appropriate
        logging method based on the given level. Supported levels include DEBUG, INFO, WARNING, ERROR,
        and CRITICAL. If an unsupported level is provided, the method defaults to logging at the INFO level.

        Args:
            level: An integer representing the logging level. Valid levels are DEBUG, INFO, WARNING, ERROR,
                and CRITICAL.
            message: A string representing the message to be logged.
            **kw: Arbitrary keyword arguments that can be passed to the corresponding logger method.

        """
        if level == self.DEBUG:
            self._logger.debug(message, **kw)
        elif level == self.INFO:
            self._logger.info(message, **kw)
        elif level == self.WARNING:
            self._logger.warning(message, **kw)
        elif level == self.ERROR:
            self._logger.error(message, **kw)
        elif level == self.CRITICAL:
            self._logger.critical(message, **kw)
        else:
            self._logger.info(message, **kw)

    def debug(self, message: str, **kw: Any):
        """Log a debug level con context opzionale"""
        self._logger.debug(message, **kw)

    def info(self, message: str, **kw: Any):
        """Log a info level con context opzionale"""
        self._logger.info(message, **kw)

    def warning(self, message: str, **kw: Any):
        """Log a warning level con context opzionale"""
        self._logger.warning(message, **kw)

    def error(self, message: str, **kw: Any):
        """Log a error level con context opzionale"""
        self._logger.error(message, **kw)

    def critical(self, message: str, **kw: Any):
        """Log a critical level con context opzionale"""
        self._logger.critical(message, **kw)

    def exception(self, message: str, **kw: Any):
        """
        Log un'eccezione con traceback completo.
        Da usare all'interno di un except block.
        """
        self._logger.exception(message, **kw)

    def _check_is_root(self) -> bool:
        """
        Check if current logger is the root logger.
        Returns:
            bool: True if current logger is the root logger, False otherwise.
        """
        return StructLogger._root_logger == self.logger_name

    def set_level(self, level: int):
        """
        Imposta il livello di logging per questo specifico logger.
        """
        with self._lock:
            if not hasattr(StructLogger, "_logger_levels"):
                StructLogger._logger_levels = {}

            StructLogger._logger_levels[self.logger_name] = self._parse_level(level)

            if self._check_is_root():
                # If this is the root logger, also update the global configured level to match it
                StructLogger._global_config["level"] = self._parse_level(level)


def get_logger(
    name=None,
    verbose=True,
    structured: bool = True,
    level: Optional[int] = None,
    is_root: bool = False,
    log_file: Optional[str] = None,
    **initial_context: Any,
) -> BaseLogger:
    """
    Creates and returns a logger instance.

    If `structured` is set to True, returns an instance of `StructLogger`. Otherwise,
    returns an instance of `Logger`. Allows optionally passing logging context that
    will be used by the logger.

    Arguments:
        name (str, optional): The name of the logger. Defaults to None.
        verbose (bool): Indicates if verbose logging is enabled. Defaults to True.
        structured (bool): Indicates if a structured logger should be created.
            Defaults to True.
        level (int): The initial logging level (Logger.DEBUG, Logger.INFO, etc.). Defaults to None.
        is_root (bool): If True, configures the logger as the root logger.
            Defaults to False.
        log_file (str, optional): Path to the log file. If provided, logs will be written to this file.
        **initial_context (Any): Additional keyword arguments defining initial logging
            context.

    Returns:
        Any: An instance of `StructLogger` if `structured` is True, otherwise an
        instance of `Logger`.

    Example:
    ::
        from utils.logging import get_logger

        # Get a structured logger
        logger = get_logger(__name__, structured=True)
        logger.info("Structured log message", user_id=123, action="login")

        # Get a traditional logger with file output
        simple_logger = get_logger(__name__, structured=False, log_file="/var/log/app.log")
        simple_logger.info("Simple log message")
    """
    if structured:
        return StructLogger(
            name=name,
            verbose=verbose,
            level=level,
            log_file=log_file,
            is_root=is_root,
            **initial_context,
        )
    return Logger(name=name, verbose=verbose, level=level, log_file=log_file)
