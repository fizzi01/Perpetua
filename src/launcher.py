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
import sys
from argparse import ArgumentParser, Namespace
from pathlib import Path
from typing import Optional

from config import ApplicationConfig
from utils.cli import DaemonArguments
from utils.logging import get_logger

IS_WINDOWS = sys.platform in ("win32", "cygwin")
IS_LINUX = sys.platform.startswith("linux")
COMPILED = "__compiled__" in globals()


class DaemonRunner:
    """
    Handles starting the daemon, writing PID file, logging, and graceful shutdown.
    """

    def __init__(self, args: Optional[Namespace] = None):
        self.main_path = ApplicationConfig.get_main_path()
        # PID and log files are runtime/state, not config — keep them out of
        # the XDG config dir on Linux. On macOS/Windows these helpers fold
        # back to ``main_path`` so the layout is unchanged.
        self.pid_file = Path(
            os.path.join(ApplicationConfig.get_runtime_path(), "daemon.pid")
        )
        log_file = ApplicationConfig.get_default_log_file()
        self.log_file = Path(log_file) if log_file else None
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

    def run(self):
        """Run the daemon in this process."""
        from daemon import main as daemon_main

        self.clean_log_file()
        self.write_pid()
        self.log.info("Daemon loop started", pid=os.getpid())

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
            try:
                self.log.info("Daemon loop stopped", pid=os.getpid())
                self.cleanup_pid()
            except Exception as e:
                print(e)


def main():
    from utils.screen import Screen

    Screen.hide_icon()

    parser = ArgumentParser(description="Perpetua Daemon")
    DaemonArguments(parent=parser)
    args = parser.parse_args()

    daemon = None
    try:
        daemon = DaemonRunner(args=args)
        # Permission handling lives in the daemon now: it binds the command
        # socket first (so the GUI always connects) and drives the guided
        # permission flow over IPC. Blocking here would prevent the socket
        # from ever coming up when a permission is missing.
        daemon.run()
    except KeyboardInterrupt:
        print("Interrupted")
        sys.exit(130)
    except Exception:
        import traceback

        print(f"Fatal error: {traceback.format_exc()}")
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
