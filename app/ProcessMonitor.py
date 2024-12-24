import sys
from threading import Thread, Event
from multiprocessing import Process, Pipe
from typing import Optional, Any, List


class ProcessMonitor(Thread):
    def __init__(self,
                 exit_event: Event,
                 start_event: Event,
                 processes: Optional[List[Process] | Process] = None,
                 threads: Optional[List[Thread] | Thread] = None,
                 pipes: Optional[List | Pipe | Any] = None,
                 on_stop_callback=None):

        super().__init__(daemon=True)

        self.exit_event = exit_event
        self.start_event = start_event
        self.processes = processes
        self.threads = threads
        self.pipes = pipes
        self.on_stop_callback = on_stop_callback

    def run(self):
        # Attende l'exit_event
        self.exit_event.wait()

        # Termina i processi
        if self.processes:
            if isinstance(self.processes, list):
                for process in self.processes:
                    process.terminate()
                    process.join()
            else:
                self.processes.terminate()
                self.processes.join()

        if not self.start_event.is_set():
            self.start_event.set()  # Allow the main thread to exit

        # forza la chiusura input
        try:
            sys.stdin.close()
        except (OSError, ValueError, EOFError, BrokenPipeError) as e:
            pass

        # Chiudi i pipe
        for pipe in self.pipes:

            try:
                pipe.send(None)
            except (OSError, ValueError, EOFError, BrokenPipeError) as e:
                pass

            pipe.close()

        # Chiudi i thread
        if self.threads:
            if isinstance(self.threads, list):
                for thread in self.threads:
                    thread.join()
            else:
                self.threads.join()

        if self.on_stop_callback:
            self.on_stop_callback()
