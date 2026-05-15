"""
Daemon runtime endpoint discovery helpers.

The daemon writes a small JSON file after it successfully binds its
command socket, so that the GUI (and any other tooling: CLI, diagnostics,
external scripts) can find the daemon regardless of:

- platform (Unix socket path vs Windows TCP port);
- the user customising ``ApplicationConfig.DEFAULT_DAEMON_PORT``;
- the daemon falling back to a different port after EADDRINUSE.

The file lives under ``<main_path>/runtime/daemon.endpoint`` and is removed
on graceful shutdown. Stale files left by a crash are harmless: callers
should always verify the endpoint is reachable before assuming it's the
live daemon.

Endpoint format on disk is JSON::

    {
      "endpoint": "tcp://127.0.0.1:55652" | "unix:///path/to/socket",
      "pid": 1234,
      "started_at": "2026-05-15T15:30:00.123456+00:00",
      "version": "1.0.0"
    }

For tooling that doesn't want to parse JSON, the same string is also written
to ``daemon.endpoint.txt`` next to it.
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

    Returns the (json_path, txt_path) that were written.
    """
    json_path, txt_path = get_endpoint_paths(main_path)
    payload = {
        "endpoint": endpoint,
        "pid": os.getpid(),
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    if version:
        payload["version"] = version

    # Write JSON atomically so a partial read never lands on a torn file.
    tmp_json = json_path + ".tmp"
    with open(tmp_json, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass
    os.replace(tmp_json, json_path)

    tmp_txt = txt_path + ".tmp"
    with open(tmp_txt, "w", encoding="utf-8") as f:
        f.write(endpoint + "\n")
    os.replace(tmp_txt, txt_path)

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
