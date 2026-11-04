"""Systemd notification uses the service-manager socket, never a user-supplied API URL."""
import os
import socket

def notify(message):
    address=os.getenv('NOTIFY_SOCKET')
    if not address:return
    if address.startswith('@'):address='\0'+address[1:]
    try:
        with socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM) as sock:
            sock.settimeout(.25);sock.connect(address);sock.sendall(message.encode())
    except OSError:pass
