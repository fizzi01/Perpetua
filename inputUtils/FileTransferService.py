import os
from threading import Thread, Event
import urllib.parse
import zlib
import base64
from datetime import datetime
from queue import Queue, Empty
from typing import Callable, Dict, Any, Optional
from utils.Logging import Logger
from utils.net.netData import format_command
from utils.Interfaces import IMessageService, IServerContext, IFileTransferService, IClientContext


class FileTransferService(IFileTransferService):
    """
    Gestisce la logica del file transfer secondo la nuova architettura:
    - handle_file_copy(): broadcasting
    - handle_file_request(): bridging se owner è su un client remoto
    - handle_file_start()/chunk()/end() bridging e/o salvataggio locale
    - notifica all'utente prima di inviare file_request
    """

    def __init__(self, message_service: IMessageService, context: IServerContext | IClientContext,
                 notification_manager: Callable[[str, Callable, Callable], None] | None = None):
        """
        :param notification_manager: Funzione che visualizza una notifica all'utente con on_accept/on_reject
                                     Esempio: notification_manager("Vuoi incollare file X?", on_accept, on_reject)
        """
        super().__init__()

        self.io_manager = message_service
        self.context = context
        self.notify_user = notification_manager  # callback che mostra la notifica
        self.log = Logger.get_instance().log

        self._running = False

        # Info del file corrente
        self.current_file_info: Dict[str, Any] = {}  # file_name, file_size, file_path, owner, ecc.
        self.save_path = ""  # Path di salvataggio del file

        self.writer_thread: Thread | None = None  # Thread per la scrittura del file
        self.is_being_processed = Event()  # Flag per indicare che il file è in fase di scrittura
        self.is_file_end = Event()  # Flag per indicare che il file è finito (file_end command)
        self.stop_writer: bool = True
        self.chunk_queue = Queue()
        self.chunk_dict: Dict[int, any] = {}
        self.next_chunk_index = 0

        # Bridge state: se il server sta facendo bridging tra due client, li memorizza
        self.bridge_active = Event()
        self.transfer_active = Event()
        self.bridge_owner: Optional[str] = None  # screen del client proprietario
        self.bridge_requester: Optional[str] = None  # screen del client che richiede
        self.log_bridge = False  # Se True, logga i forward

    def start(self) -> None:
        self._running = True

    def join(self, timeout: int = 0) -> None:
        self._running = False
        self.transfer_active.clear()
        self.bridge_active.clear()
        self.stop_writer = True
        self.is_being_processed.clear()
        self.is_file_end.clear()

        # Clear the chunk queue
        while not self.chunk_queue.empty():
            self.chunk_queue.get()

        self.chunk_dict.clear()
        self.next_chunk_index = 0

        if self.writer_thread and self.writer_thread.is_alive():
            self.writer_thread.join()

    def stop(self) -> None:
        self.join()

    def is_alive(self) -> bool:
        return self._running

    def set_save_path(self, save_path):
        self.save_path = save_path

    def get_save_path(self):
        return self.save_path

    def handle_file_paste(self, file_path: str):
        """
        Riceviamo il file_path dal client e lo inviamo al server.
        """
        self.set_save_path(file_path)
        self.log(f"[FileTransferService] File paste: {file_path}")

        if isinstance(self.context, IServerContext):
            requester = self.SERVER_REQUEST
        else:
            requester = self.CLIENT_REQUEST

        self.log(f"[FileTransferService] Handling file_paste to {requester}")
        self.handle_file_request(requester)

    # ------------------------------------------------------------------------
    #  LOGICA PRINCIPALE
    # ------------------------------------------------------------------------

    def handle_file_copy(self, file_name: str, file_size: int, file_path: str,
                         local_owner: str | None = None, caller_screen: str | None = None):
        """
        Viene chiamata quando un client (o il server) fa ctrl+c su un file.
        1) Se è un client, setta owner=caller_screen sul server, e il server broadcasta file_copied a tutti gli altri.
        2) Se è il server, setta owner=LOCAL_SERVER_OWNERSHIP e manda file_copied a tutti i client (owner=EXTERNAL_OWNERSHIP).
        """
        # Sanitize the file path and name
        file_name = urllib.parse.unquote(file_name)
        file_path = urllib.parse.unquote(file_path)

        # Reparse the file name and path for network transmission
        file_name_parsed = urllib.parse.quote(file_name)
        file_path_parsed = urllib.parse.quote(file_path)

        if local_owner:  # LOCAL_OWNERSHIP or LOCAL_SERVER_OWNERSHIP
            # File copiato da un client o dal server localmente
            owner = local_owner
            self.log(f"[FileTransferService] Local copied file: {file_name}")

            if local_owner is self.LOCAL_SERVER_OWNERSHIP:  # Server ha copiato un file e broadcasta
                # Broadcast a tutti i client: file_copied <file_name> <file_size> <file_path>
                cmd = format_command(f"file_copied {file_name_parsed} {file_size} {file_path_parsed}")
                self.io_manager.send_file_copy("all", cmd)  # Tutti i client memorizzeranno con EXTERNAL_OWNERSHIP
            else:   # Client ha copiato un file, manda al server per memorizzare
                cmd = format_command(f"file_copied {file_name_parsed} {file_size} {file_path_parsed}")
                self.io_manager.send_file_copy(None, cmd)

            # Salva internamente
            self.current_file_info = {
                "owner": owner,
                "file_name": file_name_parsed,
                "file_size": file_size,
                "file_path": file_path_parsed
            }

        else:  # Received by a Server from a client
            # Un client 'caller_screen' ha copiato un file
            # Il server riceve 'file_copied' e imposta owner=caller_screen
            self.log(f"[FileTransferService] Client {caller_screen} copied file: {file_name}")
            self.current_file_info = {
                "owner": caller_screen,  # Lo screen del client
                "file_name": file_name_parsed,
                "file_size": file_size,
                "file_path": file_path_parsed
            }
            # Broadcast a tutti i client per far salvare info con EXTERNAL_OWNERSHIP
            cmd = format_command(f"file_copied {file_name_parsed} {file_size} {file_path_parsed}")
            self.io_manager.send_file_copy("all", cmd)  # Invio a tutti tanto se lo stesso client lo riceve, lo ignora

    def handle_file_copy_external(self, file_name: str, file_size: int, file_path: str):
        """
        Quando un client diverso dal proprietario riceve file_copied in broadcast, salva owner=EXTERNAL_OWNERSHIP.
        """
        # Sanitize the file path and name
        file_name = urllib.parse.unquote(file_name)
        file_path = urllib.parse.unquote(file_path)

        file_name_parsed = urllib.parse.quote(file_name)
        file_path_parsed = urllib.parse.quote(file_path)

        # Check if the file is already registered
        if self.current_file_info.get("file_path", "") == file_path_parsed:
            self.log(f"[FileTransferService] File already registered: {file_path}")
            return

        self.log(f"[FileTransferService] Registered external file copy: {file_path}")
        self.current_file_info = {
            "owner": self.EXTERNAL_OWNERSHIP,
            "file_name": file_name_parsed,
            "file_size": file_size,
            "file_path": file_path_parsed
        }

    def handle_file_request(self, requester_screen: str):
        """
        1) Se il file è di ownership=EXTERNAL_OWNERSHIP e io sono un client, devo chiedere conferma all'utente
           e poi mandare 'file_request' al Server.
        2) Se sono il server e ricevo file_request, vedo se l'owner è local_server o un altro client.
           - Se l'owner è local_server, mando subito file_start. (oppure notifico utente server?)
           - Se l'owner è un client “A”, attivo bridging: salvo bridge_owner=A, bridge_requester=requester_screen
             e inoltro `file_request` a A.
        """

        owner = self.current_file_info.get("owner", "")
        file_path = self.current_file_info.get("file_path", "")
        file_name = self.current_file_info.get("file_name", "")
        file_size = self.current_file_info.get("file_size", 0)

        if file_path == "" or owner == "":
            self.log("[FileTransferService] No file info available", 2)
            return

        self.log(f"[FileTransferService] File request from {requester_screen}. "
                 f"\n File: {file_path}")

        file_path_raw = urllib.parse.unquote(file_path)

        # Am i server?
        if isinstance(self.context, IServerContext):

            # Avoiding auto-requests
            if requester_screen == self.SERVER_REQUEST and owner == self.LOCAL_SERVER_OWNERSHIP:
                self.log("[FileTransferService] Server: Avoiding auto-request", Logger.DEBUG)
                return

            if requester_screen == self.SERVER_REQUEST:  # Il server vuole il file
                self.log("[FileTransferService] Server: file_request from server")

                def on_accept():
                    self.log(f"[FileTransferService] Server: Sending file_request to {owner}.")
                    self.io_manager.send_file_request(owner, format_command(f"file_request {file_path_raw}"))

                def on_reject():
                    self.log("[FileTransferService] Server: User refused to request file.")

                msg = f"Vuoi incollare il file {file_name} ({file_size} bytes) dal server?"
                self._ask_permission(msg, on_accept, on_reject)

            # Se owner=LOCAL_SERVER_OWNERSHIP, inizio l'upload (mandando file_start)
            elif owner == self.LOCAL_SERVER_OWNERSHIP:
                self.log(f"[FileTransferService] Server: sending file_start to {requester_screen}")
                self.io_manager.send_file(file_path=file_path_raw, screen=requester_screen)

            # Il server fa bridging
            elif owner != self.EXTERNAL_OWNERSHIP:
                # `owner` deve essere uno screen di un client
                self.log("[FileTransferService] Activating bridging mode server.")
                self.bridge_active.set()
                self.bridge_owner = owner
                self.bridge_requester = requester_screen

                # Inoltra file_request a owner
                forward_cmd = format_command(f"file_request {file_path_raw}")
                self.io_manager.send_file_request(owner, forward_cmd)
                self.log_bridge = True
                self.log(f"[FileTransferService] Forwarded file_request from {requester_screen} to {owner}")

            else:
                # Altri scenari
                pass

        else:  # I am a client

            # Avoiding auto-request
            if requester_screen == self.CLIENT_REQUEST and owner == self.LOCAL_OWNERSHIP:
                self.log("[FileTransferService] Client: Avoiding auto-request", Logger.DEBUG)
                return

            if owner == self.LOCAL_OWNERSHIP:
                self.log(f"[FileTransferService] Client: sending file_start to Server")
                self.io_manager.send_file(file_path=file_path_raw, screen=self.SERVER_REQUEST)

            elif owner == self.EXTERNAL_OWNERSHIP:
                # Avoid multiple requests
                if self.is_being_processed.is_set():
                    self.log("[FileTransferService] File transfer already in progress", Logger.WARNING)
                    return  # File transfer already in progress

                # Siamo un client con EXTERNAL_OWNERSHIP → devo chiedere conferma UTENTE prima di inviargli `file_request`
                def on_accept():
                    # Mando file_request al server
                    cmd = format_command(f"file_request {file_path}")
                    self.log(f"[FileTransferService] Sending file request to Server: {cmd}")
                    self.io_manager.send_file_request("server", cmd)

                def on_reject():
                    self.log("[FileTransferService] User refused to request file.")

                msg = f"Vuoi incollare il file {file_name} ({file_size} bytes) dal server?"
                self._ask_permission(msg, on_accept, on_reject)

            else:
                # Altri scenari
                pass

    def handle_file_start(self, from_screen: str, file_name: str, file_size: int):
        """
        Riceviamo 'file_start'. Se server in bridging, forward al requester, altrimenti se sono client,
        chiedo conferma all'utente prima di avviare la ricezione (o l'upload).
        """
        # Sanitize the file name
        encoded_file_name = urllib.parse.unquote(file_name)

        file_name = urllib.parse.quote(encoded_file_name)

        self.log(
            f"[FileTransferService] Starting receiving file from {from_screen}: {encoded_file_name}, size={file_size}")

        # Se server e bridging è attivo e from_screen == bridge_owner, forward a bridge_requester
        if self.bridge_active.is_set() and from_screen == self.bridge_owner:
            forward_cmd = format_command(f"file_start {encoded_file_name} {file_size}")
            self.io_manager.send_file_request(self.bridge_requester, forward_cmd)
            self.log_bridge and self.log(
                f"[BRIDGE] Forwarded file_start from {self.bridge_owner} -> {self.bridge_requester}")
            return

        # Check if the file is already register
        if self.current_file_info.get("file_name", "") != file_name:
            self.log(
                f"[FileTransferService] File not registered, can't start transferring. \n"
                f"File -> {encoded_file_name}")
            return

        # Confirm the file transfer
        self.transfer_active.set()

        try:

            # Reset the file transfer state
            # TODO: Move this to a separate function
            self.is_file_end.clear()
            self.is_being_processed.set()
            self.chunk_dict.clear()
            self.next_chunk_index = 0

            # Check if the file already exists, if so change the name
            if os.path.exists(os.path.join(self.save_path, encoded_file_name)):
                # Takes the file name and adds a number to it, to avoid overwriting
                file_name, file_extension = os.path.splitext(encoded_file_name)
                current_date = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                file_name = file_name + current_date + file_extension
                self.save_path = os.path.join(self.save_path, file_name)
            else:
                self.save_path = os.path.join(self.save_path, encoded_file_name)

            if not self.writer_thread or not self.writer_thread.is_alive():
                self.writer_thread = Thread(target=self._write_chunks, daemon=True)
                self.stop_writer = False
                self.writer_thread.start()

            self.log(f"[FileTransferService] File transfer started: {self.save_path}")
        except OSError as e:
            self.log(f"[FileTransferService] Error opening file: {e}", Logger.ERROR)
            self.is_being_processed.clear()
            self.stop_writer = True
            self.transfer_active.clear()
        except Exception as e:
            self.log(f"[FileTransferService] Error starting file transfer: {e}", Logger.ERROR)
            self.is_being_processed.clear()
            self.stop_writer = True
            self.transfer_active.clear()

    def handle_file_chunk(self, from_screen: str, encoded_chunk: str, chunk_index: int):
        """
        Se bridging è attivo e from_screen == owner, forward chunk a bridge_requester. Viceversa se from_screen=requester,
        forward chunk an owner. Altrimenti lo elaboriamo localmente.
        """
        if self.bridge_active.is_set():
            # Se from_screen=bridge_owner, forward a bridge_requester
            if from_screen == self.bridge_owner:
                forward_cmd = format_command(f"file_chunk {encoded_chunk} {chunk_index}")
                self.io_manager.forward_file_data(self.bridge_requester, forward_cmd)
                self.log_bridge and self.log(
                    f"[BRIDGE] Forward chunk {chunk_index} from {self.bridge_owner} -> {self.bridge_requester}")
            elif from_screen == self.bridge_requester:
                # Riservato per usi futuri per ack, etc.
                pass
            else:
                # Non dovrebbe capitare in bridging
                pass
        else:
            # Siamo noi il destinatario, salviamo su disco
            self.log(f"[FileTransferService] (Local) File chunk index={chunk_index} processing.")
            try:
                # Trait string "b'...'" as a byte string
                chunk_data = encoded_chunk.replace("b'", "").replace("'", "")
                chunk_data = base64.b64decode(chunk_data)

                decompressed_chunk = zlib.decompress(chunk_data)  # Decomprime il chunk
            except Exception as e:
                self.log(f"[FileTransferService] Error decompressing file chunk: {e}", Logger.ERROR)
                self.is_file_end.set()
                self.is_being_processed.clear()
                return

            # Inserisce il chunk nella coda con l'indice
            self.chunk_queue.put((int(chunk_index), decompressed_chunk))

    def handle_file_end(self, from_screen: str):
        """
        Se bridging è attivo, forward file_end all'altro peer, disattivare bridging e finire.
        """
        if self.bridge_active.is_set():
            if from_screen == self.bridge_owner:
                # forward a bridge_requester
                forward_cmd = format_command("file_end")
                self.io_manager.forward_file_data(self.bridge_requester, forward_cmd)
                self.log_bridge and self.log(f"[BRIDGE] Forward file_end -> {self.bridge_requester}")
            elif from_screen == self.bridge_requester:
                # Riservato per usi futuri, ack di ricezione, etc.
                # forward_cmd = format_command("file_end")
                # self.io_manager.forward_file_data(self.bridge_owner, forward_cmd)
                # self.log_bridge and self.log(f"[BRIDGE] Forward file_end -> {self.bridge_owner}")
                pass

            self.bridge_active.clear()
            self.log(f"[FileTransferService] Bridging ended.")
            self.bridge_owner = None
            self.bridge_requester = None
        else:
            self.log("[FileTransferService] Transfer completed.")
            self.is_file_end.set()
            self.is_being_processed.clear()
            self.transfer_active.clear()

    def _write_chunks(self):
        max_iterations = 20  # Maximum number of iterations to wait for the file to reach the expected size
        iteration_count = 0

        if not self.transfer_active.is_set():
            self.log("[FileTransferService] Transfer not accepted, writer stopped.")
            return

        while not self.stop_writer:
            self.log("[FileTransferService] Writing file chunks to disk.")
            try:
                chunk_index, chunk_data = self.chunk_queue.get(timeout=1)
                self.chunk_dict[chunk_index] = chunk_data

                while self.next_chunk_index in self.chunk_dict:
                    try:
                        with open(self.save_path, 'ab') as file:
                            file.write(self.chunk_dict.pop(self.next_chunk_index))
                        self.next_chunk_index += 1
                    except Exception as e:
                        self.log(f"{e}", Logger.ERROR)

                        try:
                            os.remove(self.save_path)
                        except OSError:
                            pass

                        self.is_being_processed.clear()
                        self.chunk_dict.clear()
                        self.next_chunk_index = 0
                        self.stop_writer = True
                        return

                    # Check if the file is completely downloaded
                    if os.path.getsize(self.save_path) == self.current_file_info.get("file_size", 0):
                        self.log(f"[FileTransferService] File transfer completed: {self.save_path}")
                        self.is_being_processed.clear()
                        self.transfer_active.clear()
                        self.chunk_dict.clear()
                        self.next_chunk_index = 0
                        iteration_count = 0  # Reset iteration count
                        self.log("[FileTransferService] Writer stopped.")
                        return  # Exit the thread

            except Empty:
                if (not self.is_being_processed.is_set()) and self.is_file_end.is_set():
                    iteration_count += 1
                    if iteration_count >= max_iterations:
                        self.log("[FileTransferService] File size did not reach expected size",
                                 Logger.ERROR)

                        try:
                            os.remove(self.save_path)
                        except OSError:
                            pass

                        self.is_being_processed.clear()
                        self.transfer_active.clear()
                        self.chunk_dict.clear()
                        self.next_chunk_index = 0
                        return
                continue
            except Exception as e:
                self.log(f"[FileTransferService] Error writing file chunk: {e}", Logger.ERROR)
                self.is_being_processed.clear()
                self.transfer_active.clear()

        self.log("[FileTransferService] Writer thread stopped.")

    # --------------------------------------------------------------
    # Esempio di funzione che mostra la logica di notifica con callback
    # --------------------------------------------------------------
    def _ask_permission(self, message: str, on_accept: Callable, on_reject: Callable):
        """
        Usa la notify_user callback per chiedere conferma all’utente.
        """
        if self.context:  # Avoiding IDE Stupid warning
            # self.notify_user(message, on_accept, on_reject)
            print(message)
            on_accept()
