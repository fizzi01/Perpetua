from multiprocessing import connection
from threading import Thread
from queue import Queue, Empty
from typing import Callable, Optional, Any


class IOManager(Thread):
    """
    Classe generica che gestisce un canale di input/output basato su:
    - Pipe (multiprocessing.Pipe)
    - Oppure una coda (Queue) di Python (thread-safe).

    In base al parametro 'mode', può comportarsi come:
      - "reader": Legge da Pipe/Queue e scrive i messaggi in uno stream di output.
      - "input":  Legge dallo stream (es. sys.stdin) e invia i messaggi su Pipe/Queue.
    """
    READER_MODE = "reader"
    INPUT_MODE = "input"

    def __init__(
            self,
            name: str,
            pipe: Optional[connection.Connection] = None,
            queue: Optional[Queue] = None,
            mode: str = "",
            input_stream: Optional[Callable] = None,
            output_stream: Optional[Callable] = None,
            on_message_callback: Optional[Callable[[Any], None]] = None,
            on_error_callback: Optional[Callable[[Exception], None]] = None,
            auto_start: bool = False,
            polling_interval: float = 0.05
    ):
        """
        :param name: Nome univoco dell'IOManager, per logging o debug.
        :param pipe: Connessione di una pipe se si vuole lavorare con Pipe (di multiprocessing).
        :param queue: Oggetto queue.Queue o multiprocessing.Queue se si preferisce usare la coda.
        :param mode: "reader" o "input". Se "reader", legge da Pipe/Queue e scrive su 'stream'.
                     Se "input", legge da 'stream' e invia a Pipe/Queue.
        :param input_stream: Stream di input (ad es. sys.stdin) da cui leggere.
        :param output_stream: Stream di output su cui scrivere i messaggi letti da Pipe/Queue.
        :param on_message_callback: Funzione da richiamare quando viene letto un messaggio.
                                   (usata in modalità "reader" per elaborazioni extra)
        :param on_error_callback: Funzione da richiamare in caso di errore di lettura.
        :param auto_start: Se True, l'IOManager avvia subito il thread di lettura/scrittura.
        :param polling_interval: Intervallo (in secondi) di polling per leggere messaggi.
        """
        super().__init__(daemon=True)
        self.name = name
        self.pipe_conn = pipe
        self.queue_obj = queue
        self.mode = mode
        self.output_stream = output_stream  # External output channel (es. sys.stdout)
        self.input_stream = input_stream  # External input channel (es. sys.stdin)

        self.on_message_callback = on_message_callback
        self.on_error_callback = on_error_callback
        self.polling_interval = polling_interval

        self._running = False

        # Verify pipe_conn XOR queue_obj
        if (pipe and queue) or (not pipe and not queue):
            raise ValueError("Specify either pipe_conn or queue_obj, not both or none.")

        if auto_start:
            self.start()

    def start(self):
        if self._running:
            return
        self._running = True
        super().start()

    def join(self, timeout=2.0):
        self._running = False
        self._close_pipe()
        self._close_queue()
        super().join(timeout=timeout)

    def _close_pipe(self):
        if self.pipe_conn:
            self.pipe_conn.close()
        self.pipe_conn = None

    def _close_queue(self):
        if self.queue_obj:
            self.queue_obj.put(None)
            self.queue_obj.empty()
        self.queue_obj = None

    def run(self):
        """
        Se mode="reader", legge da Pipe/Queue e invia i messaggi allo stream (o invoca on_message_callback).
        Se mode="input", legge dallo stream (ad es. sys.stdin) e invia i messaggi su Pipe/Queue.
        """
        if self.mode == self.READER_MODE:
            self._reader_loop()
        elif self.mode == self.INPUT_MODE:
            self._input_loop()
        else:
            raise ValueError(
                f"[{self.name}] mode='{self.mode}' non valid. Use {self.READER_MODE} or {self.INPUT_MODE}.")

    def _reader_loop(self):
        """
        Legge da Pipe/Queue, invoca on_message_callback e/o scrive su stream.
        """
        while self._running:
            try:
                if self.pipe_conn:
                    # Waiting for message from pipe
                    msg = self.pipe_conn.recv()
                    self._handle_message(msg)
                elif self.queue_obj:
                    # Waiting for message from pipe
                    try:
                        msg = self.queue_obj.get()
                        self._handle_message(msg)
                    except Empty:
                        pass
            except ValueError:
                break
            except EOFError:
                break
            except Exception as e:
                if self.on_error_callback:
                    self.on_error_callback(e)
                else:
                    print(f"[{self.name}] Error in loop: {e}")

        print(f"[{self.name}] Exiting reader loop.")

    def _input_loop(self):
        """
        Legge da uno stream di input (es. sys.stdin) e invia i messaggi a Pipe/Queue.
        """
        if not self.output_stream:
            print(f"[{self.name}] No input stream defined. Exiting.")
            return

        while self._running:
            try:
                if self.pipe_conn:
                    input_msg = self.pipe_conn.recv()  # wait for input request through pipe
                    value = self._handle_message(input_msg)
                    self.pipe_conn.send(value)  # Forward Inputted value
                elif self.queue_obj:
                    input_msg = self.queue_obj.get()
                    value = self._handle_message(input_msg)
                    self.queue_obj.put(value)

                # Se on_message_callback è definita, la usiamo per elaborazioni extra
                if self.on_message_callback:
                    self.on_message_callback("")
            except ValueError:
                break
            except EOFError:
                break
            except Exception as e:
                if self.on_error_callback:
                    self.on_error_callback(e)
                else:
                    print(f"[{self.name}] Error in loop: {e}")

        print(f"[{self.name}] Exiting input loop.")

    def _handle_message(self, msg) -> Optional[Any]:
        """
        In modalità 'reader', gestisce il messaggio letto da Pipe/Queue.
        """

        if self.mode == self.INPUT_MODE:
            msg = "" if msg is None else msg
            value = self.input_stream(msg)
        else:
            value = msg
            if value:
                self.output_stream(msg)

        return value

    def send(self, msg: Any):
        """
        Manda un messaggio sulla pipe o sulla coda, se presente.
        Utilizzata soprattutto in modalità "input", ma volendo anche in "reader".
        """
        if self.pipe_conn:
            try:
                self.pipe_conn.send(msg)
            except Exception as e:
                if self.on_error_callback:
                    self.on_error_callback(e)
        elif self.queue_obj:
            try:
                self.queue_obj.put(msg)
            except Exception as e:
                if self.on_error_callback:
                    self.on_error_callback(e)

    def is_alive(self) -> bool:
        return self._running and super().is_alive()
