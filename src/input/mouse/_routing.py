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
"""Client-space routing math shared by the client and the server.

The client runs it against its own cursor. For a client that cannot read its
cursor (Wayland: a libei sender gets no feedback) the server runs the same math
in ``AbsolutePointerTracker`` against a position it owns, and sends the result
as absolute coordinates.
"""

from dataclasses import dataclass
from typing import Optional, Protocol, Sequence

#: Distance from the server edge where a returning cursor lands, in px.
RETURN_EDGE_MARGIN = 6
#: Outward push needed to leave through the edge the cursor entered by, in px.
RETURN_PUSH_MARGIN = 12

_OUTWARD = {"left": ("x", -1), "right": ("x", 1), "top": ("y", -1), "bottom": ("y", 1)}


class MonitorRect(Protocol):
    monitor_id: int
    min_x: int
    min_y: int
    max_x: int
    max_y: int


def _axis_norm(monitor: MonitorRect, edge: str, x: float, y: float) -> float:
    if edge in ("left", "right"):
        span = max(1, monitor.max_y - monitor.min_y)
        value = (y - monitor.min_y) / span
    else:
        span = max(1, monitor.max_x - monitor.min_x)
        value = (x - monitor.min_x) / span
    return max(0.0, min(1.0, value))


def lookup_return_to_server(
    bindings: Sequence[dict],
    server_bbox: Optional[Sequence[float]],
    monitor: Optional[MonitorRect],
    edge: str,
    x: float,
    y: float,
) -> Optional[tuple[float, float]]:
    """Server landing for leaving ``monitor`` through ``edge`` at ``(x, y)``.

    Normalised over ``server_bbox`` (the server's virtual desktop), landing
    ``RETURN_EDGE_MARGIN`` px inside the bound server monitor edge. ``None``
    when no edge binding covers that point.
    """
    if not bindings or monitor is None or server_bbox is None:
        return None
    axis_norm = _axis_norm(monitor, edge, x, y)

    for b in bindings:
        if b.get("client_monitor_id") != monitor.monitor_id:
            continue
        if b.get("client_edge") != edge:
            continue
        c_start = b.get("client_axis_start", 0.0)
        c_end = b.get("client_axis_end", 0.0)
        c_span = c_end - c_start
        if c_span <= 0 or not (c_start <= axis_norm < c_end):
            continue

        local_norm = (axis_norm - c_start) / c_span
        s_start = b.get("server_axis_start", 0.0)
        s_end = b.get("server_axis_end", 0.0)
        server_axis = s_start + local_norm * (s_end - s_start)

        s_min_x = int(b.get("server_monitor_min_x", 0))
        s_min_y = int(b.get("server_monitor_min_y", 0))
        s_max_x = int(b.get("server_monitor_max_x", 0))
        s_max_y = int(b.get("server_monitor_max_y", 0))
        s_edge = b.get("server_edge")
        s_w = max(1, s_max_x - s_min_x)
        s_h = max(1, s_max_y - s_min_y)
        if s_edge == "right":
            target_x = s_max_x - RETURN_EDGE_MARGIN
            target_y = s_min_y + server_axis * s_h
        elif s_edge == "left":
            target_x = s_min_x + RETURN_EDGE_MARGIN
            target_y = s_min_y + server_axis * s_h
        elif s_edge == "bottom":
            target_x = s_min_x + server_axis * s_w
            target_y = s_max_y - RETURN_EDGE_MARGIN
        elif s_edge == "top":
            target_x = s_min_x + server_axis * s_w
            target_y = s_min_y + RETURN_EDGE_MARGIN
        else:
            continue

        bx0, by0, bx1, by1 = server_bbox
        bw = max(1, bx1 - bx0)
        bh = max(1, by1 - by0)
        x_norm = max(0.0, min(1.0, (target_x - bx0) / bw))
        y_norm = max(0.0, min(1.0, (target_y - by0) / bh))
        return x_norm, y_norm

    return None


def resolve_intra_warp(
    candidates: Sequence[dict],
    monitor: Optional[MonitorRect],
    edge: str,
    x: float,
    y: float,
) -> Optional[tuple[int, float, float, str]]:
    """``(dst_monitor_id, x, y, dst_edge)`` for leaving ``monitor`` through ``edge``.

    ``candidates`` are the intra-client bindings whose source is ``monitor``.
    Lands just inside the destination edge so the next move doesn't re-trigger
    the warp from the other side. ``None`` when no binding covers the point.
    """
    if monitor is None or not candidates:
        return None
    axis_norm = _axis_norm(monitor, edge, x, y)

    for b in candidates:
        if b.get("src_edge") != edge:
            continue
        s_start = b.get("src_axis_start", 0.0)
        s_end = b.get("src_axis_end", 0.0)
        if s_end <= s_start or not (s_start <= axis_norm < s_end):
            continue

        local = max(0.0, min(1.0, (axis_norm - s_start) / (s_end - s_start)))
        d_start = b.get("dst_axis_start", 0.0)
        d_end = b.get("dst_axis_end", 0.0)
        dst_axis = d_start + local * (d_end - d_start)

        dst_id = int(b.get("dst_monitor_id", -1))
        dst_edge = b.get("dst_edge")
        d_min_x = int(b.get("dst_monitor_min_x", 0))
        d_min_y = int(b.get("dst_monitor_min_y", 0))
        d_max_x = int(b.get("dst_monitor_max_x", 0))
        d_max_y = int(b.get("dst_monitor_max_y", 0))
        d_w = max(1, d_max_x - d_min_x)
        d_h = max(1, d_max_y - d_min_y)

        if dst_edge == "right":
            target_x, target_y = d_max_x - 2, d_min_y + dst_axis * d_h
        elif dst_edge == "left":
            target_x, target_y = d_min_x + 1, d_min_y + dst_axis * d_h
        elif dst_edge == "bottom":
            target_x, target_y = d_min_x + dst_axis * d_w, d_max_y - 2
        elif dst_edge == "top":
            target_x, target_y = d_min_x + dst_axis * d_w, d_min_y + 1
        else:
            continue
        return dst_id, target_x, target_y, dst_edge

    return None


def exit_edge(monitor: MonitorRect, x: float, y: float) -> Optional[str]:
    """Edge of ``monitor`` that ``(x, y)`` lies beyond (half-open box), or None."""
    if x < monitor.min_x:
        return "left"
    if x >= monitor.max_x:
        return "right"
    if y < monitor.min_y:
        return "top"
    if y >= monitor.max_y:
        return "bottom"
    return None


@dataclass(frozen=True)
class Position:
    """Absolute cursor position in the client's desktop space."""

    x: int
    y: int


@dataclass(frozen=True)
class Return:
    """Leave the client; land at ``(x, y)`` normalised over the server bbox."""

    x: float
    y: float


class AbsolutePointerTracker:
    """The cursor of a client that cannot report it, kept by the server.

    Same decisions the client makes in ``_check_edge``, on a position the
    server owns: return through an edge binding (through the entry edge only
    after ``RETURN_PUSH_MARGIN`` of outward push), warp through an intra-client
    binding, otherwise clamp to the monitor the cursor is on. Clamping per
    monitor, not to the bounding box, keeps the model where the compositor
    keeps the real cursor on layouts with uneven monitors.
    """

    def __init__(self):
        self._monitors: dict[int, MonitorRect] = {}
        self._bindings: list[dict] = []
        self._intra_by_src: dict[int, list[dict]] = {}
        self._server_bbox: Optional[Sequence[float]] = None
        self._monitor: Optional[MonitorRect] = None
        self._x = 0.0
        self._y = 0.0
        self._locked_edge: Optional[str] = None
        self._outward_push = 0.0

    @property
    def active(self) -> bool:
        return self._monitor is not None

    @property
    def position(self) -> Optional[Position]:
        if self._monitor is None:
            return None
        return Position(int(round(self._x)), int(round(self._y)))

    def set_topology(
        self,
        monitors: Sequence[MonitorRect],
        edge_bindings: Sequence[dict],
        intra_bindings: Sequence[dict],
        server_bbox: Optional[Sequence[float]],
    ) -> None:
        self._monitors = {m.monitor_id: m for m in monitors}
        self._bindings = list(edge_bindings)
        self._intra_by_src = {}
        for b in intra_bindings:
            self._intra_by_src.setdefault(int(b.get("src_monitor_id", -1)), []).append(
                b
            )
        self._server_bbox = server_bbox
        if self._monitor is not None:
            current = self._monitors.get(self._monitor.monitor_id)
            if current is None:
                self._monitor = None  # its monitor went away
            else:
                self._monitor = current
                self._clamp()

    def activate(
        self,
        monitor_id: Optional[int],
        x_norm: float,
        y_norm: float,
        entry_edge: Optional[str],
    ) -> Optional[Position]:
        """Start tracking at the activation landing.

        ``x_norm``/``y_norm`` are normalised over the landing monitor, the
        same values the client denormalises; ``(-1, -1)`` (hotkey switch)
        lands at its centre. ``None`` when the client has no usable monitor.
        """
        monitor = self._monitors.get(monitor_id) if monitor_id is not None else None
        if monitor is None and self._monitors:
            monitor = next(iter(self._monitors.values()))
        if monitor is None:
            self._monitor = None
            return None
        self._monitor = monitor
        width = monitor.max_x - monitor.min_x
        height = monitor.max_y - monitor.min_y
        if x_norm < 0 or y_norm < 0:
            x_norm = y_norm = 0.5
        self._x = monitor.min_x + x_norm * width
        self._y = monitor.min_y + y_norm * height
        self._clamp()
        self._locked_edge = entry_edge
        self._outward_push = 0.0
        return self.position

    def deactivate(self) -> None:
        self._monitor = None
        self._locked_edge = None
        self._outward_push = 0.0

    def move(self, dx: float, dy: float):
        """Apply a relative move: a ``Position``, a ``Return``, or None if idle."""
        monitor = self._monitor
        if monitor is None:
            return None
        x, y = self._x + dx, self._y + dy
        edge = exit_edge(monitor, x, y)
        self._update_push(edge, dx, dy)
        if edge is None:
            self._x, self._y = x, y
            return self.position

        gate_open = edge != self._locked_edge or (
            self._outward_push >= RETURN_PUSH_MARGIN
        )
        if gate_open:
            target = lookup_return_to_server(
                self._bindings, self._server_bbox, monitor, edge, x, y
            )
            if target is not None:
                self.deactivate()
                return Return(*target)

        warp = resolve_intra_warp(
            self._intra_by_src.get(monitor.monitor_id, ()), monitor, edge, x, y
        )
        if warp is not None:
            dst_id, tx, ty, _ = warp
            dst = self._monitors.get(dst_id)
            if dst is not None:
                self._monitor = dst
                self._x, self._y = tx, ty
                # A warp is a new crossing; the entry lock belonged to the old one.
                self._locked_edge = None
                self._outward_push = 0.0
                self._clamp()
                return self.position

        self._x, self._y = x, y
        self._clamp()
        return self.position

    def _update_push(self, edge: Optional[str], dx: float, dy: float) -> None:
        # Mirrors the client: only a continuous push against the entry edge
        # counts. Pushing back in or leaving the edge starts over; sliding
        # along it neither adds nor resets.
        if self._locked_edge is None:
            return
        axis, sign = _OUTWARD[self._locked_edge]
        outward = (dx if axis == "x" else dy) * sign
        if edge != self._locked_edge:
            if not (outward == 0 and self._on_edge(self._locked_edge)):
                self._outward_push = 0.0
            return
        if outward < 0:
            self._outward_push = 0.0
        elif outward > 0:
            self._outward_push = min(
                self._outward_push + outward, float(RETURN_PUSH_MARGIN)
            )

    def _on_edge(self, edge: str) -> bool:
        m = self._monitor
        return {
            "left": self._x <= m.min_x,
            "right": self._x >= m.max_x - 1,
            "top": self._y <= m.min_y,
            "bottom": self._y >= m.max_y - 1,
        }[edge]

    def _clamp(self) -> None:
        m = self._monitor
        self._x = max(m.min_x, min(m.max_x - 1, self._x))
        self._y = max(m.min_y, min(m.max_y - 1, self._y))
