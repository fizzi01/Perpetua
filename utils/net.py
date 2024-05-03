import socket


def get_local_ip() -> str:
    """
    Get the local IP address of the current device
    :return: The local IP address of the current device
    """
    return socket.gethostbyname(socket.gethostname())
