from attr import dataclass

@dataclass
class StreamType:
    """Tipi di stream QUIC con priorità"""
    COMMAND = 0      # Alta priorità - comandi bidirezionali
    KEYBOARD = 4     # Alta priorità - eventi tastiera
    MOUSE = 1        # Media priorità - movimenti mouse (alta frequenza)
    CLIPBOARD = 12   # Bassa priorità - clipboard
    FILE = 16        # Bassa priorità - trasferimenti file
