# Strategy Pattern per la gestione del logging
import threading
from datetime import datetime


class LoggingStrategy:
    def log(self, stdout, message, priority):
        raise NotImplementedError("Subclasses should implement this!")


class ConsoleLoggingStrategy(LoggingStrategy):
    def log(self, stdout, message, priority):
        cur_time = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        if priority == Logger.WARNING:
            stdout(f"\033[93m[{cur_time}] WARNING: {message}\033[0m")
        elif priority == Logger.ERROR:
            stdout(f"\033[91m[{cur_time}] ERROR: {message}\033[0m")
        elif priority == Logger.INFO:
            stdout(f"\033[94m[{cur_time}] INFO: {message}\033[0m")
        elif priority == Logger.DEBUG:
            stdout(f"\033[92m[{cur_time}] DEBUG: {message}\033[0m")
        else:
            stdout(f"[{cur_time}] ", message)


class SilentLoggingStrategy(LoggingStrategy):
    def log(self, stdout, message, priority):
        if priority == Logger.ERROR:
            stdout(f"\033[91mERROR: {message}\033[0m")
        elif priority == Logger.INFO:
            stdout(f"\033[94mINFO: {message}\033[0m")
        elif priority == Logger.WARNING:
            stdout(f"\033[93mWARNING: {message}\033[0m")
        else:
            pass


class LoggingStrategyFactory:
    @staticmethod
    def get_logging_strategy(logging_enabled):
        if logging_enabled:
            return ConsoleLoggingStrategy()
        else:
            return SilentLoggingStrategy()


class Logger:
    _instance = None
    _lock = threading.Lock()

    WARNING = 3
    ERROR = 2
    INFO = 1
    DEBUG = 0

    def __new__(cls, logging, stdout):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super(Logger, cls).__new__(cls)
                    cls._instance._initialize(logging, stdout)
        return cls._instance

    def _initialize(self, logging, stdout=print):
        self.logging = logging
        self.stdout = stdout
        self.logging_strategy = LoggingStrategyFactory.get_logging_strategy(self.logging)

    def log(self, message, priority: int = 0):
        self.logging_strategy.log(self.stdout, message, priority)

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            raise Exception("Logger not initialized.")
        return cls._instance
