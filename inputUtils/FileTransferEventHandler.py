import os
import threading
from queue import Queue, Empty
import urllib.parse

from network.IOManager import QueueManager
from utils.Logging import Logger
from utils.netData import format_command


class FileTransferEventHandler:
    _instance = None
    _lock = threading.Lock()

    DOWNLOAD_START_EVENT = 'download_start'
    UPLOAD_START_EVENT = 'upload_start'
    PROGRESS_EVENT = 'progress'
    DOWNLOAD_STOP_EVENT = 'download_stop'
    UPLOAD_STOP_EVENT = 'upload_stop'

    SERVER_REQUEST = 'server'
    CLIENT_REQUEST = 'client'
    LOCAL_OWNERSHIP = 'client'
    LOCAL_SERVER_OWNERSHIP = 'server'

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            with cls._lock:
                if not cls._instance:
                    cls._instance = super(FileTransferEventHandler, cls).__new__(cls)
                    cls._instance._initialize()
        return cls._instance

    def _initialize(self):
        self.event_queue = Queue()
        self.lock = threading.Lock()
        self.current_file_info = {}
        self.save_path = None

        self.owner = None
        self.requester = None

        self.to_forward = threading.Event()
        self.is_being_processed = threading.Event()

        self.file_data = bytearray()
        self.file = None

        self.io_manager = QueueManager(None)
        self.log = Logger.get_instance().log

    def set_actors(self, owner, requester):
        with self.lock:
            self.owner = owner
            self.requester = requester

    def set_save_path(self, save_path):
        with self.lock:
            self.save_path = save_path

    def get_save_path(self):
        with self.lock:
            return self.save_path

    def save_file_info(self, owner: str = "", file_path: str = "", file_size: int = 0, file_hash: str = None,
                       file_type: str = "", file_name: str = "", file_extension: str = ""):
        self.log(f"Invoke save_file_info: {owner}, {file_path}, {file_size}, {file_hash}, {file_type}, {file_name}, {file_extension}")

        self.current_file_info = {
            'owner': owner,
            'file_path': file_path,
            'file_size': file_size,
            'file_hash': file_hash,
            'file_type': file_type,
            'file_name': file_name,
            'file_extension': file_extension
        }

    def start_download(self, file_path):
        if not self.current_file_info:
            return

        if file_path and file_path != self.current_file_info['file_path']:
            return

        if self.is_being_processed.is_set():
            return

        with self.lock:
            self.event_queue.put((self.DOWNLOAD_START_EVENT, file_path))
            self.is_being_processed.set()

    def start_upload(self, file_path):
        if not self.current_file_info:
            return

        if file_path and file_path != self.current_file_info['file_path']:
            return

        if self.is_being_processed.is_set():
            return

        with self.lock:
            self.event_queue.put((self.UPLOAD_START_EVENT, file_path))
            self.is_being_processed.set()

    def stop_download(self, file_path):
        if not self.current_file_info:
            return

        if file_path and file_path != self.current_file_info['file_path']:
            return

        if not self.is_being_processed.is_set():
            return

        with self.lock:
            self.event_queue.put((self.DOWNLOAD_STOP_EVENT, file_path))
            self.is_being_processed.clear()

    def stop_upload(self, file_path):
        if not self.current_file_info:
            return

        if file_path and file_path != self.current_file_info['file_path']:
            return

        if not self.is_being_processed.is_set():
            return

        with self.lock:
            self.event_queue.put((self.UPLOAD_STOP_EVENT, file_path))
            self.is_being_processed.clear()

    def update_progress(self, file_path, progress):
        with self.lock:
            self.event_queue.put((self.PROGRESS_EVENT, file_path, progress))

    def get_event(self, timeout=0.1):
        try:
            return self.event_queue.get(timeout=timeout)
        except Empty:
            return None

    def handle_file_paste(self, save_path, ownership):
        self.set_save_path(save_path)

        # Send file request to server
        self.handle_file_request(ownership)

    def handle_file_copy(self, file_info, ownership):
        with self.lock:
            self.save_file_info(
                owner=ownership,
                file_path=file_info['file_path'],
                file_size=file_info['file_size'],
                file_name=file_info['file_name'],
            )

            if ownership != self.LOCAL_SERVER_OWNERSHIP:  # If i'm not the server, i need to forward the file info
                self.io_manager.send_file_copy(None, format_command(
                    f"file_copied {file_info['file_name']} {file_info['file_size']} {file_info['file_path']}"))
                self.log(f"File copy forwarded from {ownership} to Server: {file_info['file_path']}")

            self.log(f"File copied registered from {ownership}: {file_info['file_path']}")

    def handle_file_request(self, requester):
        with self.lock:
            self.log(f"File request received from {requester}")
            file_info = self.current_file_info

            if not file_info:
                self.log("No file info available", Logger.WARNING)
                return

            owner = file_info['owner']  # Owner screen name
            file_path = urllib.parse.unquote(file_info['file_path'])

            if requester == owner:  # Block if requester is the owner
                return

            if owner == self.LOCAL_OWNERSHIP or owner == self.LOCAL_SERVER_OWNERSHIP:  # States that i'm the owner
                self.upload_file(file_path, requester)
                self.to_forward.clear()
            else:
                self.owner = owner
                self.requester = requester
                self.io_manager.send_file_request(owner, format_command(f"file_request {file_path}"))
                self.is_being_processed.set()
                self.to_forward.set() if not requester in [self.LOCAL_OWNERSHIP,
                                                           self.LOCAL_SERVER_OWNERSHIP] else self.to_forward.clear()

    def forward_file_data(self, data):
        if self.is_being_processed.is_set() and self.to_forward.is_set():
            self.io_manager.forward_file_data(self.requester, data)

    def end_file_transfer(self):
        self.is_being_processed.clear()
        self.to_forward.clear()

    def handle_file_start(self, file_info):
        with self.lock:
            self.log(f"File transfer started: {file_info['file_name']}")
            self.is_being_processed.set()  # Set the flag to start processing the file data

            encoded_file_name = urllib.parse.quote(file_info['name'])
            encoded_file_path = urllib.parse.quote(file_info['path'])

            if self.to_forward.is_set():  # Case where i'm the server, and i need to forward the file info
                self.io_manager.forward_file_data(self.requester, format_command(
                    f"file_start {encoded_file_name} {file_info['file_size']} {encoded_file_path}"))
            else:  # Case where i'm the requester, and i received the file info
                self.save_file_info(
                    file_path=encoded_file_path,
                    file_size=file_info['file_size'],
                    file_name=encoded_file_name,
                )

                try:
                    self.save_path = os.path.join(self.save_path, encoded_file_name)
                    self.file = open(self.save_path, 'wb')
                    self.log(f"File transfer started: {self.save_path}")
                except Exception as e:
                    self.log(f"Error opening file: {e}", Logger.ERROR)
                    self.is_being_processed.clear()

    def handle_file_chunk(self, chunk_data):
        with self.lock:
            if not self.is_being_processed.is_set():
                return

            if self.to_forward.is_set():  # Case where i'm the server, and i need to forward the file data
                self.forward_file_data(chunk_data)
            else:  # Case where i'm the requester, and i received the file data
                try:
                    self.file.write(bytes.fromhex(chunk_data))
                    self.log(f"File chunk written: {len(chunk_data)} bytes")
                except Exception as e:
                    self.log(f"Error writing file chunk: {e}", Logger.ERROR)
                    self.file.close()
                    os.remove(self.save_path)
                    self.is_being_processed.clear()

    def handle_file_end(self):
        with self.lock:
            if not self.is_being_processed.is_set():
                return

            if self.to_forward.is_set():
                self.io_manager.forward_file_data(self.requester, format_command("file_end"))
                self.to_forward.clear()
            else:
                # Complete the file transfer
                try:
                    # Check if the file is completely downloaded
                    if os.path.getsize(self.save_path) == self.current_file_info['size']:
                        self.log(f"File transfer completed: {self.save_path}")
                    else:
                        raise Exception("File transfer incomplete, file size mismatch")

                    self.file.close()
                except Exception as e:
                    self.log(f"[FILE TRANSFER] {e}", Logger.ERROR)
                    os.remove(self.save_path)

            self.is_being_processed.clear()

    def upload_file(self, file_path, requester):
        self.io_manager.send_file(file_path, requester)
        self.is_being_processed.set()
