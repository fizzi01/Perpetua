#  Perpatua - open-source and cross-platform KVM software.
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

import argparse
from typing import Any


def DaemonArguments(parent: argparse.ArgumentParser | None = None, socket_default: str | None = None
                    ) -> argparse.ArgumentParser | argparse._ArgumentGroup:
    """Parse command-line arguments"""

    if parent:
        parser = parent.add_argument_group("Daemon Options")
    else:
        parser = argparse.ArgumentParser(description="Daemon")

    parser.add_argument(
        "--socket",
        default=socket_default,
        help="Socket path (Unix socket) or host:port (TCP on Windows)",
    )
    parser.add_argument("--config-dir", help="Configuration directory path")
    parser.add_argument("--debug", action="store_true", help="Enable debug directory")
    parser.add_argument(
        "--log-terminal", action="store_true", help="Log only to stdout"
    )
    return parser
