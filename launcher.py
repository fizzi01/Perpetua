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

import asyncio
import os
import signal
import sys
from argparse import ArgumentParser, Namespace
from pathlib import Path
from typing import Optional

from config import ApplicationConfig
from utils.cli import DaemonArguments
from utils.logging import get_logger
from utils.permissions import PermissionChecker

IS_WINDOWS = sys.platform in ("win32", "cygwin")
IS_LINUX = sys.platform.startswith("linux")
COMPILED = "__compiled__" in globals()


class DaemonRunner:
    """
    Handles starting the daemon, writing PID file, logging, and graceful shutdown.
    """

    def __init__(self, args: Optional[Namespace] = None):
        self.main_path = ApplicationConfig.get_main_path()
        self.pid_file = Path(os.path.join(self.main_path, "daemon.pid"))
        self.log_file = Path(
            os.path.join(
                self.main_path,
                ApplicationConfig.get_default_log_file() or "daemon.log",
            )
        )
        self._args = args
        # If --log-terminal, write logs to stdout only (no file)
        if self._args and self._args.log_terminal:
            self.log_file = None
        self._log = None

    @property
    def log(self):
        """Lazy initialization of logger."""
        if self._log is None:
            self._log = get_logger(
                self.__class__.__name__, verbose=True, log_file=self.log_file
            )
        return self._log

    def clean_log_file(self):
        """Clean up old log file if it exists."""
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

    def write_pid(self):
        """Write current PID to file."""
        self.pid_file.parent.mkdir(parents=True, exist_ok=True)
        self.pid_file.write_text(str(os.getpid()))

    def cleanup_pid(self):
        """Remove PID file."""
        self.pid_file.unlink(missing_ok=True)

    def check_permissions(self) -> bool:
        """Check and request necessary permissions."""
        permission_checker = PermissionChecker(self.log)  # type: ignore
        permissions = permission_checker.get_missing_permissions()
        if len(permissions) > 0:
            for permission in permissions:
                if permission.can_request:
                    self.log.info(
                        "Requesting missing permissions", permission=permission
                    )
                    result = permission_checker.request_permission(
                        permission.permission_type
                    )
                    if not result.is_granted:
                        self.log.error("Permission not granted", permission=permission)
                        return False
                else:
                    self.log.error(
                        f"Missing permission ({permission.message})",
                        permission=permission.permission_type.name,
                    )
                    return False
        return True

    def run(self):
        """Run the daemon in this process."""
        from service.daemon import main as daemon_main

        self.clean_log_file()

        # Reinitialize logger as root logger with final settings
        self._log = get_logger(
            "daemon", is_root=True, verbose=True, log_file=self.log_file
        )

        self.write_pid()
        self.log.info("Daemon process started", pid=os.getpid())

        # Graceful shutdown on signals
        def on_shutdown(signum, _frame):
            self.log.info("Daemon received shutdown signal", signal=signum)
            self.cleanup_pid()
            sys.exit(0)

        signal.signal(signal.SIGTERM, on_shutdown)
        signal.signal(signal.SIGINT, on_shutdown)

        try:
            if IS_WINDOWS:
                import winloop as asyncloop  # ty:ignore[unresolved-import]
            else:
                import uvloop as asyncloop  # ty:ignore[unresolved-import]
            asyncloop.run(daemon_main())
        except ImportError:
            asyncio.run(daemon_main())
        except Exception:
            self.log.exception("Daemon failed")
            self.cleanup_pid()
            sys.exit(1)
        finally:
            self.log.info("Daemon stopped", pid=os.getpid())
            self.cleanup_pid()


def main():
    from utils.screen import Screen

    Screen.hide_icon()

    parser = ArgumentParser(description="Perpetua Daemon")
    DaemonArguments(parent=parser)
    args = parser.parse_args()

    daemon = None
    try:
        daemon = DaemonRunner(args=args)
        if not daemon.check_permissions():
            sys.exit(1)
        daemon.run()
    except KeyboardInterrupt:
        if daemon and daemon._log:
            daemon.log.info("Interrupted")
        else:
            print("Interrupted")
        sys.exit(130)
    except Exception:
        import traceback

        if daemon and daemon._log:
            daemon.log.exception("Fatal error")
            daemon.log.exception(traceback.format_exc())
        else:
            print(f"Fatal error: {traceback.format_exc()}")
        sys.exit(1)


if __name__ == "__main__":
    main()
