import asyncio
import os
import subprocess
import sys
from multiprocessing import Process
from pathlib import Path
from time import sleep

sys.path.insert(0, str(Path(__file__).parent / "src"))
from utils.logging import get_logger

log = get_logger("launcher", verbose=True)


def _run_daemon():
    from service.daemon import main, IS_WINDOWS

    try:
        if IS_WINDOWS:
            import winloop as asyncloop
        else:
            import uvloop as asyncloop
        asyncloop.run(main())
    except ImportError:
        asyncio.run(main())
    except Exception:
        log.exception("Daemon failed")
        sys.exit(1)


def start_daemon() -> Process:
    process = Process(target=_run_daemon, daemon=False)
    process.start()
    log.info(f"Daemon started", pid=process.pid)
    return process


def start_gui(executable_dir: str) -> bool:
    gui_path = os.path.join(executable_dir, 'perpetua')
    
    if not (os.path.isfile(gui_path) and os.access(gui_path, os.X_OK)):
        log.error("GUI not found", path=gui_path)
        return False
    
    subprocess.Popen([gui_path], close_fds=True)
    log.info("GUI started", path=gui_path)
    return True


def main():
    daemon = start_daemon()
    if not daemon.is_alive():
        log.error("Failed to start daemon")
        return 1
    
    sleep(1)
    start_gui(os.path.dirname(sys.executable))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log.info("Interrupted")
        sys.exit(130)
    except Exception:
        log.exception("Fatal error")
        sys.exit(1)
