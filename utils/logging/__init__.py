import logging
import sys
import threading
from datetime import datetime


class ColoredFormatter(logging.Formatter):
    """Formatter con colori per output console"""

    COLORS = {
        'DEBUG': '\033[92m',    # Green
        'INFO': '\033[94m',     # Blue
        'WARNING': '\033[93m',  # Yellow
        'ERROR': '\033[91m',    # Red
        'CRITICAL': '\033[1;31m',   # Dark Bold Red
        'RESET': '\033[0m'
    }

    def format(self, record):
        # Format timestamp
        cur_time = datetime.fromtimestamp(record.created).strftime("%H:%M:%S.%f")[:-3]

        # Get color for level
        color = self.COLORS.get(record.levelname, '')
        reset = self.COLORS['RESET']

        # Format message
        return f"{color}[{cur_time}] [{record.levelname}]: {record.getMessage()}{reset}"


class SilentFormatter(logging.Formatter):
    """Formatter per modalità silent (solo errori e info importanti)"""

    COLORS = {
        'INFO': '\033[94m',     # Blue
        'WARNING': '\033[93m',  # Yellow
        'ERROR': '\033[91m',    # Red
        'RESET': '\033[0m'
    }

    def format(self, record):
        # Show only ERROR, WARNING, INFO without timestamp
        if record.levelno >= logging.INFO:
            color = self.COLORS.get(record.levelname, '')
            reset = self.COLORS['RESET']
            return f"{color}{record.levelname}: {record.getMessage()}{reset}"
        return ""


class Logger:
    """
    Wrapper del package logging di Python che preserva l'API legacy.
    """
    _instance = None
    _lock = threading.Lock()

    # Priority constants (mappati ai livelli di logging)
    DEBUG = 0
    INFO = 1
    WARNING = 2
    ERROR = 3
    CRITICAL = 4

    def __new__(cls, log=True, stdout=None):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super(Logger, cls).__new__(cls)
                    cls._instance._initialize(log, stdout)
        return cls._instance

    def _initialize(self, log=True, stdout=None):
        """Inizializza il logger usando il package logging di Python"""
        self.logging_enabled = logging
        self.stdout = stdout or print

        # Crea logger interno
        self._logger = logging.getLogger('pyContinuity')
        self._logger.handlers.clear()  # Rimuovi handler esistenti
        self._logger.propagate = False

        # Crea handler per console
        handler = logging.StreamHandler(sys.stdout)

        # Imposta formatter in base alla modalità
        if log:
            formatter = ColoredFormatter()
            self._logger.setLevel(logging.DEBUG)
            handler.setLevel(logging.DEBUG)
        else:
            formatter = SilentFormatter()
            self._logger.setLevel(logging.INFO)
            handler.setLevel(logging.INFO)

        handler.setFormatter(formatter)
        self._logger.addHandler(handler)

    def set_level(self, level: int):
        """
        Imposta il livello di logging.

        Args:
            level: Priority level (DEBUG=0, INFO=1, ERROR=2, WARNING=3)
        """
        # Mappa custom priority ai livelli logging
        level_map = {
            self.DEBUG: logging.DEBUG,
            self.INFO: logging.INFO,
            self.ERROR: logging.ERROR,
            self.WARNING: logging.WARNING,
            self.CRITICAL: logging.CRITICAL
        }

        logging_level = level_map.get(level, logging.INFO)
        self._logger.setLevel(logging_level)

        # Aggiorna anche gli handler
        for handler in self._logger.handlers:
            handler.setLevel(logging_level)

    def log(self, message, priority: int = 0):
        """
        Log un messaggio con la priorità specificata.

        Args:
            message: Messaggio da loggare
            priority: Priority level (DEBUG=0, INFO=1, ERROR=2, WARNING=3)
        """
        # Mappa custom priority ai livelli logging
        if priority == self.DEBUG:
            self._logger.debug(message)
        elif priority == self.INFO:
            self._logger.info(message)
        elif priority == self.ERROR:
            self._logger.error(message)
        elif priority == self.CRITICAL:
            self._logger.critical(message)
        elif priority == self.WARNING:
            self._logger.warning(message)
        else:
            # Default a INFO per valori non riconosciuti
            self._logger.info(message)

    # Metodi di convenience per compatibilità
    def debug(self, message):
        """Log a debug level"""
        self.log(message, self.DEBUG)

    def info(self, message):
        """Log a info level"""
        self.log(message, self.INFO)

    def warning(self, message):
        """Log a warning level"""
        self.log(message, self.WARNING)

    def error(self, message):
        """Log a error level"""
        self.log(message, self.ERROR)

    @classmethod
    def get_instance(cls):
        """
        Ottieni l'istanza singleton del logger.

        Returns:
            Logger instance

        Raises:
            Exception: Se il logger non è stato inizializzato
        """
        if cls._instance is None:
            # Crea un'istanza di default se non esiste
            cls(log=True)
        return cls._instance


