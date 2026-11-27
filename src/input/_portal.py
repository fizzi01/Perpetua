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

"""What the XDG portals remember about Perpetua: its app id and its grants.

The portal stores a persistent grant per app id, and the restore token is how
the next session claims it without a dialog.
"""

import json
import os
import threading
from datetime import datetime, timezone

from utils.fs import atomic_write_text
from utils.logging import get_logger

TOKEN_FILE_NAME = "portal_tokens.json"
REMOTE_DESKTOP = "remote_desktop"

_log = get_logger("PortalIdentity")

_REGISTER_LOCK = threading.Lock()
_registered: str | None = None
_register_tried = False


def app_id() -> str:
    from config import ApplicationConfig

    return ApplicationConfig.app_id


def ensure_host_app_registered() -> str | None:
    """Register the app id with the portal once per process.

    Must run before the first portal object of the process: the portal ties
    the id to the D-Bus connection on its first call. Returns the id, or None
    when the portal has no Registry (xdg-desktop-portal < 1.19.4) and grants
    fall back to whatever id the portal derives from the systemd scope.
    """
    global _registered, _register_tried
    with _REGISTER_LOCK:
        if _register_tried:
            return _registered
        _register_tried = True
        wanted = app_id()
        try:
            from pyinputcapture import register_host_app

            register_host_app(wanted)
        except Exception as exc:
            _log.warning(
                "Portal app id not registered, grants may not persist",
                app_id=wanted,
                error=str(exc) or type(exc).__name__,
            )
            return None
        _registered = wanted
        _log.info("Portal app id registered", app_id=wanted)
        return wanted


class TokenStore:
    """Restore tokens on disk, one per portal, with the app id they belong to.

    Kept apart from the config file, which the GUI rewrites. The token is a
    credential: mode 0600, never logged.
    """

    def __init__(self, path: str | None = None):
        self._path = path
        self._lock = threading.Lock()

    @property
    def path(self) -> str:
        if self._path is None:
            from config import ApplicationConfig

            self._path = os.path.join(
                ApplicationConfig.get_default_state_path(), TOKEN_FILE_NAME
            )
        return self._path

    def _read(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            _log.warning("Portal token file unreadable", error=str(exc))
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, data: dict) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        atomic_write_text(self.path, json.dumps(data, indent=2), mode=0o600)

    def load(self, portal: str, current_app_id: str | None) -> str | None:
        with self._lock:
            entry = self._read().get(portal)
        if not isinstance(entry, dict) or not isinstance(entry.get("token"), str):
            return None
        saved_for = entry.get("app_id")
        if saved_for != current_app_id:
            # The grant is keyed by app id: expect a dialog this time.
            _log.warning(
                "Portal token was issued to another app id",
                portal=portal,
                token_app_id=saved_for,
                app_id=current_app_id,
            )
        return entry["token"]

    def save(self, portal: str, token: str, current_app_id: str | None) -> None:
        with self._lock:
            data = self._read()
            data[portal] = {
                "token": token,
                "app_id": current_app_id,
                "desktop": os.environ.get("XDG_CURRENT_DESKTOP"),
                "saved_at": datetime.now(timezone.utc).isoformat(),
            }
            try:
                self._write(data)
            except OSError as exc:
                _log.warning("Portal token not saved", portal=portal, error=str(exc))

    def clear(self, portal: str) -> None:
        with self._lock:
            data = self._read()
            if data.pop(portal, None) is None:
                return
            try:
                self._write(data)
            except OSError as exc:
                _log.warning("Portal token not cleared", portal=portal, error=str(exc))


_store = TokenStore()


def token_store() -> TokenStore:
    return _store
