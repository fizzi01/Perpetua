from queue import Queue
from threading import Thread, Event

from server.command import CommandFactory
from utils.Interfaces import IClientContext, IMessageService, IEventBus, IServerCommandProcessor, IFileTransferService
from utils.Logging import Logger


class ServerCommandProcessor(IServerCommandProcessor):
    def __init__(self, context, message_service: IMessageService, event_bus: IEventBus):
        super().__init__(context, message_service, event_bus)

        # Async queues
        self.file_queue = Queue()
        self.mouse_queue = Queue()
        self.keyboard_queue = Queue()
        self.clipboard_queue = Queue()
        self._thread_pool = []

        self.stop_event = Event()

        self._start_consumers()

    def _start_consumers(self):
        self.keyboard_thread = Thread(target=self._consume_queue, args=(self.keyboard_queue,), daemon=True)
        self.keyboard_thread.start()

        self.clipboard_thread = Thread(target=self._consume_queue, args=(self.clipboard_queue,), daemon=True)
        self.clipboard_thread.start()

        self.mouse_thread = Thread(target=self._consume_queue, args=(self.mouse_queue,), daemon=True)
        self.mouse_thread.start()

        self.file_thread = Thread(target=self._consume_queue, args=(self.file_queue,), daemon=True)
        self.file_thread.start()

        self._thread_pool.extend([self.keyboard_thread, self.clipboard_thread, self.mouse_thread, self.file_thread])

    def _consume_queue(self, queue):
        from queue import Empty
        while not self.stop_event.is_set():
            try:
                cmd_instance = queue.get(timeout=1)
                cmd_instance.execute()
                queue.task_done()
            except Empty:
                continue

    def process_server_command(self, command: str | tuple, screen: str | None = None):
        if not command:
            return
        cmd_instance = CommandFactory.create_command(raw_command=command, context=self.context,
                                                     message_service=self.message_service, event_bus=self.event_bus,
                                                     screen=screen)
        if not cmd_instance:
            self.logger(f"[ServerCommandProcessor] Invalid server command: {command}", Logger.ERROR)
            return

        cmd_name = cmd_instance.DESCRIPTION

        if cmd_name.startswith("file_"):
            self.file_queue.put(cmd_instance)
        elif cmd_name == "mouse":
            self.mouse_queue.put(cmd_instance)
        elif cmd_name == "keyboard":
            self.keyboard_queue.put(cmd_instance)
        elif cmd_name == "clipboard":
            self.clipboard_queue.put(cmd_instance)
        elif cmd_name == "screen":
            # Se necessario, una coda dedicata, o esegui direttamente:
            cmd_instance.execute()
        else:
            # Comando non riconosciuto
            self.logger(f"[ServerCommandProcessor] Unknown command type: {cmd_name}", Logger.WARNING)

    def stop(self):
        self.stop_event.set()
        for thread in self._thread_pool:
            if thread.is_alive():
                thread.join()
        self.stop_event.clear()
