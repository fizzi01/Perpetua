from src.utils.logging import BaseLogger
import asyncio
import os
import subprocess
import sys
from pathlib import Path
from time import sleep
from psutil import pid_exists

from utils.logging import get_logger
from utils.permissions import PermissionChecker
from config import ApplicationConfig

IS_WINDOWS = sys.platform == "win32"

# PID file to track daemon process
PID_FILE = Path(os.path.join(ApplicationConfig.get_main_path(), "daemon.pid"))

GUI_EXECUTABLE = ApplicationConfig.app_name.lower()

# Log file for daemon output
LOG_FILE = Path(os.path.join(ApplicationConfig.get_main_path(), "daemon.log"))
TEMP_LOG_FILE = Path(os.path.join(ApplicationConfig.get_main_path(), "launcher_temp.log"))

log = get_logger("launcher")

def clean_temp_log_file():
    """Remove temporary launcher log file if exists."""
    if TEMP_LOG_FILE.exists():
        try:
            TEMP_LOG_FILE.unlink()
        except Exception as e:
            print(f"Failed to remove temporary log file: {e}")

def clean_log_file():
    """Clean up old log file if exists."""
    if LOG_FILE.exists():
        try:
            LOG_FILE.unlink()
            log.info("Old log file removed", path=str(LOG_FILE))
        except Exception as e:
            log.warning("Failed to remove old log file", path=str(LOG_FILE), error=str(e))


def get_daemon_pid() -> int | None:
    """Get daemon PID from file if exists and process is running."""
    if not PID_FILE.exists():
        return None

    try:
        pid = int(PID_FILE.read_text().strip())
        # Check if process is still running
        if pid_exists(pid):
            return pid
        else:
            PID_FILE.unlink(missing_ok=True)
            return None
    except (ValueError, ProcessLookupError, PermissionError):
        # Invalid PID or process not running, clean up stale PID file
        PID_FILE.unlink(missing_ok=True)
        return None


def write_daemon_pid(pid: int):
    """Write daemon PID to file."""
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(pid))


def run_daemon():
    """Run daemon in this process (called with --daemon argument)."""
    from service.daemon import main
    import signal

    # Write PID file
    write_daemon_pid(os.getpid())
    log.info("Daemon process started", pid=os.getpid())

    # Signal handler for graceful shutdown
    def signal_handler(signum, frame):
        log.info("Daemon received shutdown signal", signal=signum)
        PID_FILE.unlink(missing_ok=True)
        sys.exit(0)

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    try:
        if IS_WINDOWS:
            import winloop as asyncloop  # type: ignore
        else:
            import uvloop as asyncloop  # type: ignore
        asyncloop.run(main())
    except ImportError:
        asyncio.run(main())
    except Exception:
        log.exception("Daemon failed")
        PID_FILE.unlink(missing_ok=True)
        sys.exit(1)
    finally:
        log.info("Daemon stopped", pid=os.getpid())
        PID_FILE.unlink(missing_ok=True)


def start_daemon(executable_dir: str) -> bool:
    """Start daemon as a separate process by spawning ourselves with --daemon."""
    existing_pid = get_daemon_pid()
    if existing_pid:
        log.info("Daemon already running", pid=existing_pid)
        return True

    clean_log_file()  # Clean up old log file before starting new daemon

    self_path = os.path.join(executable_dir, "Perpetua")
    if IS_WINDOWS:
        self_path += ".exe"

    try:
        subprocess.Popen(
            [self_path, '--daemon'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True
        )

        log.info("Daemon process spawned")

        # Wait for daemon to write PID file (up to 3 seconds)
        for _ in range(6):
            sleep(0.5)
            daemon_pid = get_daemon_pid()
            if daemon_pid:
                log.info("Daemon started successfully", pid=daemon_pid)
                return True

        log.warning("Daemon started but PID file not found yet")
        return True

    except Exception as e:
        log.error("Failed to start daemon", error=str(e))
        return False


def start_gui(executable_dir: str) -> bool:
    gui_path = os.path.join(executable_dir, '_perpetua')
    if IS_WINDOWS:
        gui_path += ".exe"

    if not (os.path.isfile(gui_path) and os.access(gui_path, os.X_OK)):
        log.error("GUI not found", path=gui_path)
        return False

    g = subprocess.Popen(
        [gui_path],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True
    )
    log.info("GUI started", path=gui_path)
    g.wait()
    return True


def main():
    if '--daemon' in sys.argv:
        # Reset arguments to avoid recursion
        sys.argv = [arg for arg in sys.argv if arg != '--daemon']
        run_daemon()
        clean_temp_log_file()  # Clean up temporary log file only after daemon run
        return 0

    global log
    log = get_logger("launcher", verbose=True, log_file=str(TEMP_LOG_FILE))

    # Normal launcher flow
    permission_checker = PermissionChecker(log)  # type: ignore
    permissions = permission_checker.get_missing_permissions()
    if len(permissions) > 0:
        log.info("Requesting missing permissions", permissions=permissions)
        for permission in permissions:
            result = permission_checker.request_permission(permission.permission_type)
            if not result.is_granted:
                log.error("Permission not granted", permission=permission)
                return 1


    # Start daemon if not already running
    if not start_daemon(os.path.dirname(sys.executable)):
        log.error("Failed to start daemon")
        return 1

    # Give daemon time to initialize
    sleep(1)

    # Start GUI
    if not start_gui(os.path.dirname(sys.executable)):
        log.error("Failed to start GUI")
        return 1

    log.info("Launcher completed successfully")
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
