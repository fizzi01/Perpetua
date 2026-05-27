"""Daemon runtime endpoint discovery helpers.

The daemon writes ``<main_path>/runtime/daemon.endpoint`` (JSON + plain-text)
after binding the command socket so the GUI/tooling can find it across
platform (Unix vs TCP), custom ports, and EADDRINUSE fallback. See
``write_endpoint`` for the on-disk schema and ``read_endpoint`` for the
consumer side.
"""

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

import json
import os
from datetime import datetime, timezone
from typing import Optional, Tuple

from utils.fs import atomic_write_text

ENDPOINT_DIR_NAME = "runtime"
ENDPOINT_FILE_NAME = "daemon.endpoint"
ENDPOINT_TXT_NAME = "daemon.endpoint.txt"

ENV_ENDPOINT = "PERPETUA_DAEMON_ENDPOINT"


def get_runtime_dir(main_path: str) -> str:
    """Return (and create if missing) the runtime directory for endpoint files."""
    p = os.path.join(main_path, ENDPOINT_DIR_NAME)
    os.makedirs(p, exist_ok=True)
    return p


def get_endpoint_paths(main_path: str) -> Tuple[str, str]:
    """Return (json_path, txt_path) inside the runtime directory."""
    rt = get_runtime_dir(main_path)
    return os.path.join(rt, ENDPOINT_FILE_NAME), os.path.join(rt, ENDPOINT_TXT_NAME)


def format_unix_endpoint(socket_path: str) -> str:
    """Build the canonical ``unix://`` URL for a socket path."""
    if not socket_path.startswith("/"):
        # Relative path: still keep the unix:// scheme but as-is.
        return f"unix://{socket_path}"
    return f"unix://{socket_path}"


def format_tcp_endpoint(host: str, port: int) -> str:
    return f"tcp://{host}:{port}"


def write_endpoint(
    main_path: str,
    endpoint: str,
    version: Optional[str] = None,
) -> Tuple[str, str]:
    """Persist the current daemon endpoint to disk.

    Two files are written atomically: the JSON form (consumed by the GUI
    and tooling) plus a plain-text mirror (``daemon.endpoint.txt``) for
    scripts that don't want to parse JSON. JSON schema::

        {
          "endpoint": "tcp://127.0.0.1:55652" | "unix:///path/to/socket",
          "pid": 1234,
          "started_at": "2026-05-15T15:30:00.123456+00:00",
          "version": "1.0.0"
        }

    Returns the ``(json_path, txt_path)`` that were written.
    """
    json_path, txt_path = get_endpoint_paths(main_path)
    payload = {
        "endpoint": endpoint,
        "pid": os.getpid(),
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    if version:
        payload["version"] = version

    # Write JSON and TXT atomically so a partial read never lands on a torn
    # file and both fsync'd so a crash can't strand the .txt fallback in a
    # different state than the .json
    atomic_write_text(json_path, json.dumps(payload, indent=2))
    atomic_write_text(txt_path, endpoint + "\n")

    return json_path, txt_path


def remove_endpoint(main_path: str) -> None:
    """Delete endpoint files on graceful shutdown. Errors are swallowed."""
    json_path, txt_path = get_endpoint_paths(main_path)
    for p in (json_path, txt_path):
        try:
            os.unlink(p)
        except FileNotFoundError:
            pass
        except OSError:
            pass


def read_endpoint(main_path: str) -> Optional[str]:
    """Read the endpoint string written by a running daemon.

    Returns None if the file is missing or unreadable. Caller should still
    verify reachability before assuming the endpoint is live (stale files
    after a crash are possible).
    """
    json_path, _ = get_endpoint_paths(main_path)
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        endpoint = data.get("endpoint")
        if isinstance(endpoint, str) and endpoint:
            return endpoint
    except (OSError, ValueError):
        return None
    return None


def env_endpoint_override() -> Optional[str]:
    """Return the override endpoint from the env var, if set and non-empty."""
    v = os.environ.get(ENV_ENDPOINT, "").strip()
    return v or None


def endpoint_to_socket_path(endpoint: str) -> str:
    """Parse a ``tcp://host:port`` or ``unix:///path`` URL into the legacy
    ``socket_path`` shape used internally (``host:port`` or filesystem path)."""
    if endpoint.startswith("unix://"):
        return endpoint[len("unix://") :]
    if endpoint.startswith("tcp://"):
        return endpoint[len("tcp://") :]
    # Pass-through for anything else: callers already handle host:port and
    # filesystem paths interchangeably.
    return endpoint
