import socket


def get_hostname() -> str:
    return socket.gethostname()
