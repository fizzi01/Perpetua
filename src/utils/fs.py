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
"""Filesystem helpers used across the project.

The atomic write helpers guarantee that a target file is never observed in a
partially-written state: the data is staged in a temp file on the *same*
filesystem, fsynced, optionally chmod'd, then renamed into place via
``os.replace`` (atomic on POSIX and Windows for same-volume renames).

On Windows ``os.chmod`` only toggles the read-only bit; real access control
needs DACL manipulation — use :mod:`utils.permissions` for that.
"""

from __future__ import annotations

import os
import tempfile
from typing import Optional, Union

PathLike = Union[str, os.PathLike]


def atomic_write_bytes(
    path: PathLike,
    data: bytes,
    *,
    mode: Optional[int] = None,
) -> None:
    """Atomically write *data* to *path*.

    Args:
        path: Destination file path.
        data: Raw bytes to write.
        mode: POSIX file mode (e.g. ``0o600``). Applied to the temp file
            before the rename so the destination never appears with a wider
            permission set. On Windows the call is mostly a no-op for non
            read-only bits; this is intentional.

    The temp file is created in the same directory as *path* to keep the
    final ``os.replace`` atomic (rename across filesystems is not).
    """
    target = os.fspath(path)
    directory = os.path.dirname(target) or "."

    fd, tmp = tempfile.mkstemp(prefix=".", suffix=".tmp", dir=directory)
    try:
        try:
            os.write(fd, data)
            try:
                os.fsync(fd)
            except OSError:
                # fsync is best-effort on macOS/Windows; a transient failure
                # should not abort the write.
                pass
        finally:
            os.close(fd)

        if mode is not None:
            os.chmod(tmp, mode)

        os.replace(tmp, target)
    except BaseException:
        # On any failure before/at replace, scrub the temp so we don't leak.
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_text(
    path: PathLike,
    text: str,
    *,
    encoding: str = "utf-8",
    mode: Optional[int] = None,
) -> None:
    """Atomically write *text* to *path*, encoded with *encoding*."""
    atomic_write_bytes(path, text.encode(encoding), mode=mode)
