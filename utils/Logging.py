# Strategy Pattern per la gestione del logging
import threading


class LoggingStrategy:
    def log(self, stdout, message, priority):
        raise NotImplementedError("Subclasses should implement this!")


class ConsoleLoggingStrategy(LoggingStrategy):
    def log(self, stdout, message, priority):
        if priority == 2:
            stdout(f"\033[91mERROR: {message}\033[0m")
        elif priority == 1:
            stdout(f"\033[94mINFO: {message}\033[0m")
        elif priority == 0:
            stdout(f"\033[92mDEBUG: {message}\033[0m")
        else:
            stdout(message)


class SilentLoggingStrategy(LoggingStrategy):
    def log(self, stdout, message, priority):
        if priority == 2:
            stdout(f"\033[91mERROR: {message}\033[0m")
        elif priority == 1:
            stdout(f"\033[94mINFO: {message}\033[0m")
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

    def __new__(cls, logging, stdout):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super(Logger, cls).__new__(cls)
                    cls._instance._initialize(logging, stdout)
        return cls._instance

    def _initialize(self, logging, stdout):
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
