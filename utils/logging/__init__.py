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
        return f"{color}[{cur_time}][{record.levelname}][{record.name}] {record.getMessage()}{reset}"


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
            return f"{color}[{record.levelname}][{record.name}] {record.getMessage()}{reset}"
        return ""


class Logger:
    """
    Wrapper del package logging di Python.
    Ogni istanza è associata a un modulo specifico per tracciare l'origine dei log.
    I logger dell'applicazione sono isolati nel namespace 'pyContinuity' per non interferire con le librerie esterne.
    """
    _app_logger_configured = False
    _lock = threading.Lock()
    _app_namespace = 'PyContinuity'
    _shared_handler = None

    # Priority constants (mappati ai livelli di logging)
    DEBUG = 0
    INFO = 1
    WARNING = 2
    ERROR = 3
    CRITICAL = 4

    def __init__(self, name=None, log=True, stdout=None):
        """
        Inizializza un logger per un modulo specifico.

        Args:
            name: Nome del logger (tipicamente __name__ del modulo). Se None, usa 'pyContinuity'
            log: Se True usa ColoredFormatter con DEBUG, altrimenti SilentFormatter con INFO
            stdout: Funzione di output custom (deprecato, mantenuto per compatibilità)
        """
        self.logging_enabled = log
        self.stdout = stdout or print

        # Crea il nome del logger nel namespace dell'applicazione
        if name is None or name == '__main__':
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
                self._configure_app_logger(log)
                Logger._app_logger_configured = True
                # Disabilita i logger delle librerie esterne comuni
                #self._silence_external_loggers()

        # Aggiungi l'handler condiviso a questo logger se non ce l'ha già
        if not self._logger.handlers and Logger._shared_handler:
            self._logger.addHandler(Logger._shared_handler)

        # Imposta il livello per questo specifico logger
        if log:
            self._logger.setLevel(logging.DEBUG)
        else:
            self._logger.setLevel(logging.INFO)

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

    def _silence_external_loggers(self):
        """Silenzia i logger delle librerie esterne per evitare spam"""
        # Lista di logger comuni da silenziare (mostra solo WARNING e superiori)
        external_loggers = [
            'asyncio',
            'urllib3',
            'requests',
            'matplotlib',
            'PIL',
            'paramiko',
            'cryptography',
            'aiohttp',
            'websockets',
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
        # Mappa custom priority ai livelli logging
        level_map = {
            self.DEBUG: logging.DEBUG,
            self.INFO: logging.INFO,
            self.WARNING: logging.WARNING,
            self.ERROR: logging.ERROR,
            self.CRITICAL: logging.CRITICAL
        }

        logging_level = level_map.get(level, logging.INFO)

        # Imposta il livello del logger
        self._logger.setLevel(logging_level)

        # Aggiorna anche l'handler condiviso se esiste
        if Logger._shared_handler:
            Logger._shared_handler.setLevel(logging_level)

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

    def critical(self, message):
        """Log a critical level"""
        self.log(message, self.CRITICAL)


def get_logger(name=None, log=True):
    """
    Factory function per creare un logger.

    Args:
        name: logger name
        log: True for detailed logging, False for silent mode

    Returns:
        Logger instance

    Example:
    ::
        from utils.logging import get_logger
        logger = get_logger(__name__)
        logger.info("Message from this module")
    """
    return Logger(name=name, log=log)



