# Strategy Pattern per la gestione del logging
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