#  Perpetua - open-source and cross-platform KVM software.
#  Copyright (c) 2026 Federico Izzi.
#
#  This program is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
from typing import Optional

import asyncio
import os
import subprocess
import sys
from argparse import ArgumentParser, Namespace
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from time import sleep
from psutil import pid_exists

from utils.logging import get_logger
from utils.permissions import PermissionChecker
from utils.cli import DaemonArguments
from config import ApplicationConfig

IS_WINDOWS = sys.platform in ("win32", "cygwin")
COMPILED = "__compiled__" in globals()


class Launcher:
    """Launcher class to manage daemon and GUI processes with centralized logging."""

    def __init__(self, args: Optional[Namespace] = None):
        self.main_path = ApplicationConfig.get_main_path()
        self.pid_file = Path(os.path.join(self.main_path, "daemon.pid"))
        self.log_file = Path(
            os.path.join(
                self.main_path, ApplicationConfig.get_default_log_file() or "daemon.log"
            )
        )
        self.temp_log_file = Path(os.path.join(self.main_path, "launcher_temp.log"))
        self._args = args
        self._log = None
        self.project_root = self._get_project_root()

    @property
    def log(self):
        """Lazy initialization of logger."""
        if self._log is None:
            self._log = get_logger(
                "launcher", verbose=True, log_file=self.temp_log_file
            )
        return self._log

    def _get_project_root(self) -> Path:
        """Get the project root directory based on execution mode."""
        if COMPILED:
            # When compiled, executable is in the build directory
            return Path(sys.executable).parent.resolve()
        else:
            # When running as script, launcher.py is in the project root
            return Path(__file__).parent.resolve()

    def _get_python_executable(self) -> str:
        """Get the Python executable path for running scripts."""
        return sys.executable

    def clean_temp_log_file(self):
        """Remove temporary launcher log file if exists."""
        if self.temp_log_file and self.temp_log_file.exists():
            try:
                self.temp_log_file.unlink()
            except Exception as e:
                print(f"Failed to remove temporary log file: {e}")

    def clean_log_file(self):
        """Clean up old log file if exists."""
        if self.log_file and self.log_file.exists():
            try:
                self.log_file.unlink()
                self.log.info("Old log file removed", path=str(self.log_file))
            except Exception as e:
                self.log.warning(
                    "Failed to remove old log file",
                    path=str(self.log_file),
                    error=str(e),
                )

    def get_daemon_pid(self) -> int | None:
        """Get daemon PID from file if exists and process is running."""
        if not self.pid_file.exists():
            return None

        try:
            pid = int(self.pid_file.read_text().strip())
            # Check if process is still running
            if pid_exists(pid):
                return pid
            else:
                self.pid_file.unlink(missing_ok=True)
                return None
        except (ValueError, ProcessLookupError, PermissionError):
            # Invalid PID or process not running, clean up stale PID file
            self.pid_file.unlink(missing_ok=True)
            return None

    def write_daemon_pid(self, pid: int):
        """Write daemon PID to file."""
        self.pid_file.parent.mkdir(parents=True, exist_ok=True)
        self.pid_file.write_text(str(pid))

    def run_daemon(self):
        """Run daemon in this process (called with --daemon argument)."""
        from service.daemon import main
        import signal
        from utils.screen import Screen

        Screen.hide_icon()

        self.clean_log_file()

        if self._args and self._args.log_terminal:
            self.log_file = None

        # Reinitialize logger with daemon log file
        self._log = get_logger(
            "launcher", is_root=True, verbose=True, log_file=self.log_file
        )

        # Write PID file
        self.write_daemon_pid(os.getpid())
        self.log.info("Daemon process started", pid=os.getpid())

        # Signal handler for graceful shutdown
        def signal_handler(signum, frame):
            self.log.info("Daemon received shutdown signal", signal=signum)
            self.pid_file.unlink(missing_ok=True)
            sys.exit(0)

        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)

        try:
            if IS_WINDOWS:
                import winloop as asyncloop  # ty:ignore[unresolved-import]
            else:
                import uvloop as asyncloop  # ty:ignore[unresolved-import]
            asyncloop.run(main())
        except ImportError:
            asyncio.run(main())
        except Exception:
            self.log.exception("Daemon failed")
            self.pid_file.unlink(missing_ok=True)
            sys.exit(1)
        finally:
            self.log.info("Daemon stopped", pid=os.getpid())
            self.pid_file.unlink(missing_ok=True)

    def start_daemon(self, executable_dir: str) -> bool:
        """Start daemon as a separate process by spawning ourselves with --daemon."""
        existing_pid = self.get_daemon_pid()
        if existing_pid:
            self.log.info("Daemon already running", pid=existing_pid)
            return True

        self.clean_log_file()  # Clean up old log file before starting new daemon

        if COMPILED:
            # Running as compiled executable
            self_path = os.path.join(executable_dir, "Perpetua")
            if IS_WINDOWS:
                self_path += ".exe"

            daemon_cmd = [self_path, "--daemon"]
        else:
            # Running as Python script
            python_exe = self._get_python_executable()
            launcher_script = os.path.join(executable_dir, "launcher.py")
            daemon_cmd = [python_exe, launcher_script, "--daemon"]

        # If sys.args has other relevant args, pass them to daemon
        for arg in sys.argv[1:]:
            if arg not in ("--daemon",):
                daemon_cmd.append(arg)

        try:
            if COMPILED and "--log-terminal" not in sys.argv:
                subprocess.Popen(
                    daemon_cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                    start_new_session=True,
                    close_fds=True,
                )
            else:
                subprocess.Popen(
                    daemon_cmd,
                    start_new_session=True,
                    close_fds=True,
                )

            self.log.info(
                "Daemon process spawned", mode="compiled" if COMPILED else "script"
            )

            # Wait for daemon to write PID file (up to 3 seconds)
            for _ in range(6):
                sleep(0.5)
                daemon_pid = self.get_daemon_pid()
                if daemon_pid:
                    self.log.info("Daemon started successfully", pid=daemon_pid)
                    return True

            self.log.warning("Daemon started but PID file not found yet")
            return True

        except Exception as e:
            self.log.error("Failed to start daemon", error=str(e))
            return False

    def start_gui(self, executable_dir: str) -> bool:
        """Start GUI process."""
        if COMPILED:
            # Running as compiled executable
            gui_path = os.path.join(executable_dir, "_perpetua")
            if IS_WINDOWS:
                gui_path += ".exe"

            if not (os.path.isfile(gui_path) and os.access(gui_path, os.X_OK)):
                self.log.error("GUI executable not found", path=gui_path)
                return False

            gui_cmd = [gui_path]
        else:
            # Running as Python script - start Tauri dev server
            gui_dir = os.path.join(executable_dir, "src-gui")

            if not os.path.isdir(gui_dir):
                self.log.error("GUI directory not found", path=gui_dir)
                return False

            # Check if npm/pnpm/yarn is available
            package_manager = self._get_package_manager()
            if not package_manager:
                self.log.error("No package manager found (npm/pnpm/yarn)")
                return False

            gui_cmd = [package_manager, "run", "tauri", "dev"]

        try:
            if COMPILED:
                g = subprocess.Popen(
                    gui_cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                    start_new_session=True,
                    close_fds=True,
                )
                self.log.info("GUI started", mode="compiled")
                g.wait()
            else:
                # For dev mode, run in current process to see logs
                g = subprocess.Popen(
                    gui_cmd,
                    cwd=os.path.join(executable_dir, "src-gui"),
                    start_new_session=True,
                    close_fds=True,
                )
                self.log.info("GUI started in dev mode", mode="script", cwd=gui_cmd)
                g.wait()

            return True
        except Exception as e:
            self.log.error("Failed to start GUI", error=str(e))
            return False

    def _get_package_manager(self) -> str | None:
        """Detect available package manager."""
        for pm in ["pnpm", "npm", "yarn"]:
            try:
                result = subprocess.run(
                    [pm, "--version"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                )
                if result.returncode == 0:
                    return pm
            except (FileNotFoundError, subprocess.TimeoutExpired):
                continue
        return None

    def run(self) -> int:
        """Run the launcher: start daemon and GUI."""
        # Check permissions
        permission_checker = PermissionChecker(self.log)
        permissions = permission_checker.get_missing_permissions()
        if len(permissions) > 0:
            self.log.info("Requesting missing permissions", permissions=permissions)
            for permission in permissions:
                result = permission_checker.request_permission(
                    permission.permission_type
                )
                if not result.is_granted:
                    self.log.error("Permission not granted", permission=permission)
                    return 1

        # Determine executable directory based on execution mode
        executable_dir = str(self.project_root)

        # Start
        self.log.info("Starting daemon and GUI")

        with ThreadPoolExecutor(max_workers=2) as executor:
            # Submit both tasks concurrently
            future_gui = executor.submit(self.start_gui, executable_dir)
            sleep(0.5)
            future_daemon = executor.submit(self.start_daemon, executable_dir)

            # Wait for both to complete and check results
            daemon_success = False
            gui_success = False

            for future in as_completed([future_daemon, future_gui]):
                try:
                    result = future.result()
                    if future == future_daemon:
                        daemon_success = result
                        if not result:
                            self.log.error("Failed to start daemon")
                    elif future == future_gui:
                        gui_success = result
                        if not result:
                            self.log.error("Failed to start GUI")
                except Exception as e:
                    if future == future_daemon:
                        self.log.error("Exception starting daemon", error=str(e))
                    elif future == future_gui:
                        self.log.error("Exception starting GUI", error=str(e))

            if not daemon_success or not gui_success:
                return 1

        self.log.info(
            "Launcher completed successfully", mode="compiled" if COMPILED else "script"
        )
        return 0


if __name__ == "__main__":
    launcher = None
    launcher_parser = ArgumentParser(description="Perpetua Launcher")
    launcher_parser.add_argument(
        "--daemon", action="store_true", help="Run only the daemon process"
    )
    daemon_parser = DaemonArguments(parent=launcher_parser)

    args = launcher_parser.parse_args()
    try:
        launcher = Launcher(args=args)
        if args.daemon:
            # Reset arguments to avoid recursion
            sys.argv = [arg for arg in sys.argv if arg != "--daemon"]
            launcher.run_daemon()
            sys.exit(0)

        # Clean temp log file before initializing logger
        launcher.clean_temp_log_file()

        if args.log_terminal:
            launcher.temp_log_file = None

        # Normal launcher flow
        exit_code = launcher.run()
        sys.exit(exit_code)
    except KeyboardInterrupt:
        if launcher and launcher.log:
            launcher.log.info("Interrupted")
        else:
            print("Interrupted")
        sys.exit(130)
    except Exception:
        import traceback

        if launcher and launcher.log:
            launcher.log.exception("Fatal error")
            launcher.log.exception(traceback.format_exc())
        else:
            print(f"Fatal error: {traceback.format_exc()}")
        sys.exit(1)
