def get_local_ip():
    """Ottiene l'indirizzo IP locale della macchina usato per la connessione a Internet."""
    try:
        import socket

        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(
                ("8.8.8.8", 80)
            )  # L'IP e la porta non importano realmente, non verrà inviato nulla
            ip = s.getsockname()[0]
            return ip
    except Exception as e:
        print(f"Impossibile determinare l'IP locale: {e}")
        return None
