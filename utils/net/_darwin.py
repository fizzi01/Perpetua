

def get_local_ip():
    """Ottiene l'indirizzo IP locale dell'interfaccia en0 su macOS."""
    try:
        import netifaces
        interfaces = netifaces.interfaces()
        if 'en0' in interfaces:
            addresses = netifaces.ifaddresses('en0')
            if netifaces.AF_INET in addresses:
                ip = addresses[netifaces.AF_INET][0]['addr']
                return ip
        print("Interfaccia en0 non trovata o non ha un indirizzo IP.")
        return None
    except Exception as e:
        print(f"Impossibile determinare l'IP locale: {e}")
        return None

