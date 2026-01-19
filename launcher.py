import asyncio
import os
import subprocess
import sys
from multiprocessing import Process
from pathlib import Path
from time import sleep

sys.path.insert(0, str(Path(__file__).parent / "src"))
from utils.logging import get_logger
from utils.permissions import PermissionChecker

log = get_logger("launcher", verbose=True)


def _run_daemon():
    from service.daemon import main, IS_WINDOWS

    try:
        if IS_WINDOWS:
            import winloop as asyncloop # type: ignore
        else:
            import uvloop as asyncloop # type: ignore
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
    permission_checker = PermissionChecker(log)
    permissions = permission_checker.get_missing_permissions()
    if len(permissions)>0:
        log.info("Requesting missing permissions", permissions=permissions)
        for permission in permissions:
            result = permission_checker.request_permission(permission.permission_type)
            if not result.is_granted:
                log.error("Permission not granted", permission=permission)
                return 1

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
        import traceback
        log.exception("Fatal error")
        log.exception(traceback.format_exc())
        sys.exit(1)
