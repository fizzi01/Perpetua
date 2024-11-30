import base64
import os
import threading
from datetime import datetime
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

        self.io_manager = QueueManager(None)
        self.log = Logger.get_instance().log

        self.is_end = False

        self.chunk_queue = Queue()
        self.chunk_dict = {}
        self.next_chunk_index = 0
        self.stop_writer = False
        self.writer_thread = threading.Thread(target=self._write_chunks, daemon=True)

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

        self.current_file_info = {
            'owner': owner,
            'path': file_path,
            'size': file_size,
            'hash': file_hash,
            'type': file_type,
            'name': file_name,
            'extension': file_extension
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
        self.log(f"File paste request from {ownership}: {save_path}")
        self.set_save_path(save_path)

        # Send file request to server
        self.handle_file_request(ownership)

    def handle_file_copy(self, file_info, ownership):
        with self.lock:
            file_path = urllib.parse.quote(file_info['file_path'])
            file_name = urllib.parse.quote(file_info['file_name'])

            self.save_file_info(
                owner=ownership,
                file_path=file_path,
                file_size=file_info['file_size'],
                file_name=file_name
            )

            if ownership != self.LOCAL_SERVER_OWNERSHIP:  # If i'm not the server, i need to forward the file info
                self.io_manager.send_file_copy(None, format_command(
                    f"file_copied {file_name} {file_info['file_size']} {file_path}"))
                self.log(f"File copy forwarded from {ownership} to Server: {file_info['file_path']}")

            self.log(f"File copied registered from {ownership}: {file_info['file_path']}")

    def handle_file_request(self, requester):
        with self.lock:
            self.log(f"File request received from {requester}")
            file_info = self.current_file_info

            if not file_info and requester not in [self.LOCAL_OWNERSHIP, self.LOCAL_SERVER_OWNERSHIP]:
                self.log("No file info available", Logger.WARNING)
                return

            if requester not in [self.CLIENT_REQUEST]:
                if "owner" in file_info and "path" in file_info:
                    owner = file_info['owner']  # Owner screen name
                    file_path = urllib.parse.unquote(file_info['path'])
                else:
                    return
            else:
                owner = None
                file_path = "client_request"

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
                self.to_forward.set() if requester not in [self.LOCAL_OWNERSHIP,
                                                           self.LOCAL_SERVER_OWNERSHIP] else self.to_forward.clear()
                self.log(f"File request forwarded to {owner}: {file_path}")

    def forward_file_data(self, data, command):
        if self.is_being_processed.is_set() and self.to_forward.is_set():
            command = format_command(f"{command} {data}")
            self.io_manager.forward_file_data(self.requester, command)

    def end_file_transfer(self):
        self.is_being_processed.clear()
        self.to_forward.clear()
        self.is_end = True

    def handle_file_start(self, file_info):
        with self.lock:
            self.log(f"File transfer started: {file_info['file_name']}")
            self.is_being_processed.set()  # Set the flag to start processing the file data
            self.is_end = False

            encoded_file_name = urllib.parse.unquote(file_info['file_name'])

            if self.to_forward.is_set():  # Case where i'm the server, and i need to forward the file info
                self.io_manager.forward_file_data(self.requester, format_command(
                    f"file_start {encoded_file_name} {file_info['file_size']}"))
            else:  # Case where i'm the requester, and i received the file info
                self.save_file_info(
                    file_size=file_info['file_size'],
                    file_name=encoded_file_name,
                )

                try:
                    # Check if the file already exists, if so change the name
                    if os.path.exists(os.path.join(self.save_path, encoded_file_name)):
                        # Takes the file name and adds a number to it, to avoid overwriting
                        file_name, file_extension = os.path.splitext(encoded_file_name)
                        current_date = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                        file_name = file_name + current_date + file_extension
                        self.save_path = os.path.join(self.save_path, file_name)
                    else:
                        self.save_path = os.path.join(self.save_path, encoded_file_name)

                    if not self.writer_thread.is_alive():
                        self.writer_thread = threading.Thread(target=self._write_chunks, daemon=True)
                        self.writer_thread.start()
                        self.stop_writer = False

                    self.log(f"File transfer started: {self.save_path}")
                except Exception as e:
                    self.log(f"Error opening file: {e}", Logger.ERROR)
                    self.is_being_processed.clear()

    def _write_chunks(self):
        max_iterations = 20  # Maximum number of iterations to wait for the file to reach the expected size
        iteration_count = 0

        while not self.stop_writer:
            try:
                chunk_index, chunk_data = self.chunk_queue.get(timeout=1)
                self.chunk_dict[chunk_index] = chunk_data

                while self.next_chunk_index in self.chunk_dict:
                    with open(self.save_path, 'ab') as file:
                        file.write(self.chunk_dict.pop(self.next_chunk_index))
                    self.next_chunk_index += 1

                    # Check if the file is completely downloaded
                    if os.path.getsize(self.save_path) == self.current_file_info['size']:
                        self.log(f"File transfer completed: {self.save_path}")
                        self.is_being_processed.clear()
                        self.chunk_dict.clear()
                        self.next_chunk_index = 0
                        iteration_count = 0  # Reset iteration count
                        return # Exit the thread

            except Empty:
                if (not self.is_being_processed.is_set()) and self.is_end:
                    iteration_count += 1
                    if iteration_count >= max_iterations:
                        self.log(f"[FILE TRANSFER] File size did not reach expected size",
                                 Logger.ERROR)

                        try:
                            os.remove(self.save_path)
                        except Exception as e:
                            pass

                        self.is_being_processed.clear()
                        self.chunk_dict.clear()
                        self.next_chunk_index = 0
                        return
                continue
            except Exception as e:
                self.log(f"Error writing file chunk: {e}", Logger.ERROR)
                os.remove(self.save_path)
                self.is_being_processed.clear()

    def handle_file_chunk(self, chunk_data, command=None):
        with self.lock:
            if not self.is_being_processed.is_set():
                return

            if self.to_forward.is_set():  # Case where i'm the server, and i need to forward the file data
                self.forward_file_data(chunk_data, command)
            else:  # Case where i'm the requester, and i received the file data
                try:
                    # Extract chunk index and data
                    chunk_data, chunk_index = chunk_data
                    # Trait string "b'...'" as a byte string
                    chunk_data = chunk_data.replace("b'", "").replace("'", "")
                    chunk_data = base64.b64decode(chunk_data)
                    self.chunk_queue.put((int(chunk_index), chunk_data))

                except Exception as e:
                    self.log(f"Error writing file chunk: {e}", Logger.ERROR)
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
                self.end_file_transfer()

            self.is_being_processed.clear()

    def upload_file(self, file_path, requester):
        self.io_manager.send_file(file_path, requester)
        self.is_being_processed.set()
