#!/usr/bin/env python3
"""
Client standalone per testing - da eseguire in terminale separato.
Scrive eventi ricevuti su file per comunicare con i test.
"""
import sys
import time
import socket
import json
from queue import Queue
from threading import Thread
from pathlib import Path

# Aggiungi il path del progetto
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from network.data.MessageExchange import MessageExchange
from network.protocol.message import MessageType
from network.connection.GeneralSocket import StreamType
from utils.logging.logger import Logger


class StandaloneTestClient:
    """Client di test completamente standalone."""

    def __init__(self, server_host: str, server_port: int, screen_position: str, events_file: str):
        self.logger = Logger(stdout=print, logging=True)
        self.logger.set_level(Logger.DEBUG)

        self.server_host = server_host
        self.server_port = server_port
        self.screen_position = screen_position
        self.events_file = Path(events_file)

        self.command_socket = None
        self.mouse_socket = None
        self.keyboard_socket = None
        self.msg_exchange = MessageExchange()

        self.running = False
        self.event_count = 0

        # Inizializza file eventi
        self._init_events_file()

    def _init_events_file(self):
        """Inizializza file eventi."""
        self.events_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.events_file, 'w') as f:
            json.dump({"status": "starting", "events": [], "count": 0}, f)

    def _write_status(self, status: str, data: dict = None):
        """Scrive stato su file."""
        try:
            with open(self.events_file, 'r') as f:
                current = json.load(f)
        except:
            current = {"events": [], "count": 0}

        current["status"] = status
        current["timestamp"] = time.time()
        if data:
            current.update(data)

        with open(self.events_file, 'w') as f:
            json.dump(current, f, indent=2)

    def _append_event(self, event: dict):
        """Aggiunge evento al file."""
        try:
            with open(self.events_file, 'r') as f:
                current = json.load(f)
        except:
            current = {"events": [], "count": 0, "status": "running"}

        current["events"].append(event)
        current["count"] = len(current["events"])
        current["last_event_time"] = time.time()

        with open(self.events_file, 'w') as f:
            json.dump(current, f, indent=2)

        self.event_count += 1

    def connect(self):
        """Connette al server."""
        try:
            self._write_status("connecting")
            self.logger.log(f"Connecting to {self.server_host}:{self.server_port}...", Logger.INFO)

            # Connessione comando
            self.command_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            #self.command_socket.settimeout(10)
            self.command_socket.connect((self.server_host, self.server_port))

            self.logger.log("Command socket connected", Logger.INFO)

            self.msg_exchange.set_transport(
                self.command_socket.send,
                self.command_socket.recv
            )

            # Listen for handshake data
            receive_handshake = False
            handshake_req = None
            attempts = 0
            max_attempts = 10
            while not receive_handshake:
                handshake_req = self.msg_exchange.receive_message(instant=True)
                if not handshake_req or handshake_req.message_type != MessageType.EXCHANGE:
                    self.logger.log("Invalid handshake", Logger.ERROR)
                    self._write_status("error", {"error": "invalid_handshake"})
                    attempts += 1
                    if attempts >= max_attempts:
                        raise Exception("Max handshake attempts reached")
                    time.sleep(1)
                else:
                    receive_handshake = True

            self.logger.log(f"Received handshake: {handshake_req.payload}", Logger.INFO)

            # Invia risposta
            self.msg_exchange.send_handshake_message(
                ack=True,
                source="client",
                target="server",
                streams=[StreamType.MOUSE, StreamType.KEYBOARD],
                screen_resolution="1920x1080",
                ssl=False
            )

            time.sleep(1)

            # Connetti stream mouse
            self.mouse_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.mouse_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.mouse_socket.connect((self.server_host, self.server_port))
            self.logger.log("Mouse stream connected", Logger.INFO)

            time.sleep(0.5)

            # Connetti stream keyboard
            self.keyboard_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.keyboard_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.keyboard_socket.connect((self.server_host, self.server_port))
            self.logger.log("Keyboard stream connected", Logger.INFO)

            # Rimuovi timeout
            self.command_socket.settimeout(None)
            self.mouse_socket.settimeout(None)
            self.keyboard_socket.settimeout(None)

            self.logger.log("Fully connected!", Logger.INFO)
            self._write_status("connected")
            return True

        except Exception as e:
            self.logger.log(f"Connection failed: {e}", Logger.ERROR)
            self._write_status("error", {"error": str(e)})
            import traceback
            traceback.print_exc()
            return False

    def start_receiving(self):
        """Avvia ricezione eventi."""
        self.running = True

        recv_thread = Thread(target=self._receive_loop, daemon=True)
        recv_thread.start()

        self._write_status("receiving")
        self.logger.log("Receiving started", Logger.INFO)

    def _receive_loop(self):
        """Loop ricezione."""
        mouse_msg_exchange = MessageExchange()
        mouse_msg_exchange.set_transport(
            self.mouse_socket.send,
            self.mouse_socket.recv
        )

        while self.running:
            try:
                message = mouse_msg_exchange.receive_message(instant=True)
                if message and message.message_type == MessageType.MOUSE:
                    event_data = {
                        "type": "mouse",
                        "payload": message.payload,
                        "timestamp": time.time()
                    }
                    self._append_event(event_data)
                    self.logger.log(
                        f"[{self.event_count}] Mouse event: {message.payload}",
                        Logger.INFO
                    )
            except Exception as e:
                if self.running:
                    self.logger.log(f"Receive error: {e}", Logger.ERROR)
                    self._write_status("error", {"error": str(e)})
                break

    def run(self):
        """Esegue il client."""
        if not self.connect():
            self.logger.log("Failed to connect", Logger.ERROR)
            sys.exit(1)

        self.start_receiving()

        self.logger.log("Client running - press Ctrl+C to stop", Logger.INFO)
        self.logger.log(f"Events file: {self.events_file.absolute()}", Logger.INFO)

        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            self.logger.log("Stopping...", Logger.INFO)
            self.running = False
            self._write_status("stopped")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Standalone test client")
    parser.add_argument("--host", default="127.0.0.1", help="Server host")
    parser.add_argument("--port", type=int, default=5050, help="Server port")
    parser.add_argument("--position", default="center", help="Screen position")
    parser.add_argument("--events-file", required=True, help="File to write events to")

    args = parser.parse_args()

    client = StandaloneTestClient(args.host, args.port, args.position, args.events_file)
    client.run()
