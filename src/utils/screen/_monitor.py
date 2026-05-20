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
"""Multi-monitor data model used by the mouse listener and edge detector."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Iterable, Optional

from model.monitor import MonitorInfo


@dataclass
class MonitorLayout:
    """Aggregate of the connected displays.

    ``edge_routes`` is reserved for future user-configurable arrangements
    keyed on ``(monitor_id, edge_name)``; today edge routing is driven
    by the EdgeBinding cache on the mouse listener.
    """

    monitors: tuple[MonitorInfo, ...] = field(default_factory=tuple)
    edge_routes: dict[tuple[int, str], str] = field(default_factory=dict)

    @classmethod
    def from_bboxes(
        cls,
        bboxes: Iterable[tuple[int, int, int, int]],
        primary_index: Optional[int] = None,
    ) -> "MonitorLayout":
        monitors: list[MonitorInfo] = []
        for idx, (min_x, min_y, max_x, max_y) in enumerate(bboxes):
            monitors.append(
                MonitorInfo(
                    monitor_id=idx,
                    min_x=int(min_x),
                    min_y=int(min_y),
                    max_x=int(max_x),
                    max_y=int(max_y),
                    is_primary=(
                        idx == 0 if primary_index is None else idx == primary_index
                    ),
                )
            )
        return cls(monitors=tuple(monitors))

    @property
    def virtual_bbox(self) -> tuple[int, int, int, int]:
        """Union rect of every monitor, or ``(0, 0, 0, 0)`` if empty."""
        if not self.monitors:
            return 0, 0, 0, 0
        min_x = min(m.min_x for m in self.monitors)
        min_y = min(m.min_y for m in self.monitors)
        max_x = max(m.max_x for m in self.monitors)
        max_y = max(m.max_y for m in self.monitors)
        return min_x, min_y, max_x, max_y

    def find_monitor_at(self, x: float, y: float) -> Optional[MonitorInfo]:
        for m in self.monitors:
            if m.contains(x, y):
                return m
        return None

    def has_neighbor_left(self, monitor: MonitorInfo, y: float) -> bool:
        for m in self.monitors:
            if m.monitor_id == monitor.monitor_id:
                continue
            if m.max_x <= monitor.min_x and m.min_y <= y < m.max_y:
                # 2px snap tolerance so abutting edges count as
                # neighbours even with a rounding gap.
                if monitor.min_x - m.max_x <= 2:
                    return True
        return False

    def has_neighbor_right(self, monitor: MonitorInfo, y: float) -> bool:
        for m in self.monitors:
            if m.monitor_id == monitor.monitor_id:
                continue
            if m.min_x >= monitor.max_x and m.min_y <= y < m.max_y:
                if m.min_x - monitor.max_x <= 2:
                    return True
        return False

    def has_neighbor_top(self, monitor: MonitorInfo, x: float) -> bool:
        for m in self.monitors:
            if m.monitor_id == monitor.monitor_id:
                continue
            if m.max_y <= monitor.min_y and m.min_x <= x < m.max_x:
                if monitor.min_y - m.max_y <= 2:
                    return True
        return False

    def has_neighbor_bottom(self, monitor: MonitorInfo, x: float) -> bool:
        for m in self.monitors:
            if m.monitor_id == monitor.monitor_id:
                continue
            if m.min_y >= monitor.max_y and m.min_x <= x < m.max_x:
                if m.min_y - monitor.max_y <= 2:
                    return True
        return False


class Edge(StrEnum):
    """The four sides of a monitor that can host a crossing slot."""

    LEFT = "left"
    RIGHT = "right"
    TOP = "top"
    BOTTOM = "bottom"


@dataclass(frozen=True)
class LayoutSlot:
    """A reservable slice of one server-monitor edge.

    Slots carry a normalised segment range along the edge's secondary
    axis (Y for LEFT/RIGHT, X for TOP/BOTTOM) so a single edge can be
    split across multiple clients. ``LayoutValidator`` enforces that
    no two slots in a layout overlap.
    """

    monitor_id: int
    edge: Edge
    segment_start: float = 0.0
    segment_end: float = 1.0

    def __post_init__(self) -> None:
        if not (0.0 <= self.segment_start < self.segment_end <= 1.0):
            raise ValueError(
                f"LayoutSlot segment must satisfy 0 <= start < end <= 1, "
                f"got start={self.segment_start}, end={self.segment_end}"
            )

    def is_full_edge(self) -> bool:
        return self.segment_start == 0.0 and self.segment_end == 1.0

    def overlaps(self, other: "LayoutSlot") -> bool:
        # Touching at a single point (end == other.start) counts as
        # disjoint so a clean split at 0.5 is allowed.
        if self.monitor_id != other.monitor_id or self.edge != other.edge:
            return False
        return not (
            self.segment_end <= other.segment_start
            or other.segment_end <= self.segment_start
        )

    def contains_secondary(self, axis_norm: float) -> bool:
        return self.segment_start <= axis_norm < self.segment_end

    def to_dict(self) -> dict:
        return {
            "monitor_id": self.monitor_id,
            "edge": self.edge.value,
            "segment_start": self.segment_start,
            "segment_end": self.segment_end,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "LayoutSlot":
        return cls(
            monitor_id=int(data["monitor_id"]),
            edge=Edge(data["edge"]),
            segment_start=float(data.get("segment_start", 0.0)),
            segment_end=float(data.get("segment_end", 1.0)),
        )


@dataclass(frozen=True)
class LayoutBinding:
    """Pairs a server-side LayoutSlot with a target client (optionally pinned to a client monitor)."""

    slot: LayoutSlot
    client_uid: str
    client_monitor_id: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            "slot": self.slot.to_dict(),
            "client_uid": self.client_uid,
            "client_monitor_id": self.client_monitor_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "LayoutBinding":
        return cls(
            slot=LayoutSlot.from_dict(data["slot"]),
            client_uid=str(data["client_uid"]),
            client_monitor_id=data.get("client_monitor_id"),
        )


@dataclass
class LayoutValidator:
    """Validates the no-overlap invariant on a list of bindings."""

    known_monitor_ids: Optional[set[int]] = None

    def validate(self, bindings: Iterable[LayoutBinding]) -> tuple[bool, list[str]]:
        """Return ``(ok, errors)`` — every conflict enumerated, no short-circuit."""
        bindings = list(bindings)
        errors: list[str] = []

        if self.known_monitor_ids is not None:
            for b in bindings:
                if b.slot.monitor_id not in self.known_monitor_ids:
                    errors.append(
                        f"Unknown monitor_id={b.slot.monitor_id} in binding "
                        f"for client {b.client_uid!r}"
                    )

        for i, a in enumerate(bindings):
            for b in bindings[i + 1 :]:
                if a.slot.overlaps(b.slot):
                    errors.append(
                        f"Overlapping slots: {a.slot} (client "
                        f"{a.client_uid!r}) intersects {b.slot} "
                        f"(client {b.client_uid!r})"
                    )

        return (not errors, errors)

    def slot_for(
        self,
        bindings: Iterable[LayoutBinding],
        monitor_id: int,
        edge: Edge,
        axis_norm: float,
    ) -> Optional[LayoutBinding]:
        """Route ``(monitor_id, edge, axis_norm)`` to its binding or ``None``."""
        for b in bindings:
            if (
                b.slot.monitor_id == monitor_id
                and b.slot.edge == edge
                and b.slot.contains_secondary(axis_norm)
            ):
                return b
        return None


@dataclass(frozen=True)
class LayoutReconciliation:
    """Result of comparing a stored layout against a client's current monitor list.

    Bindings pinned to a no-longer-present client monitor land in
    ``dropped``; the caller decides whether to fall back to "any
    monitor" or prompt the user to re-bind.
    """

    kept: tuple["LayoutBinding", ...]
    dropped: tuple["LayoutBinding", ...]
    missing_monitor_ids: frozenset[int]

    @property
    def is_clean(self) -> bool:
        return not self.dropped and not self.missing_monitor_ids


def reconcile_bindings_with_client_monitors(
    bindings: Iterable[LayoutBinding],
    client_uid: str,
    client_monitor_ids: Iterable[int],
) -> LayoutReconciliation:
    """Filter ``bindings`` against the monitors ``client_uid`` currently advertises.

    Bindings for OTHER clients are kept verbatim. Bindings with
    ``client_monitor_id=None`` survive because the client picks its
    target at landing time.
    """
    known = set(client_monitor_ids)
    kept: list[LayoutBinding] = []
    dropped: list[LayoutBinding] = []
    missing: set[int] = set()

    for b in bindings:
        if b.client_uid != client_uid or b.client_monitor_id is None:
            kept.append(b)
            continue
        if b.client_monitor_id in known:
            kept.append(b)
        else:
            dropped.append(b)
            missing.add(b.client_monitor_id)

    return LayoutReconciliation(
        kept=tuple(kept),
        dropped=tuple(dropped),
        missing_monitor_ids=frozenset(missing),
    )


# Slack for the "abutting" check, in OS pixels: without it a 1px
# rounding from the GUI silently disconnects adjacent boxes.
_ABUTMENT_TOLERANCE_PX = 2


_OPPOSITE_EDGE: dict = {
    Edge.LEFT: Edge.RIGHT,
    Edge.RIGHT: Edge.LEFT,
    Edge.TOP: Edge.BOTTOM,
    Edge.BOTTOM: Edge.TOP,
}


@dataclass(frozen=True)
class EdgeBinding:
    """One adjacency between a server monitor edge and a client monitor.

    Carries both sides of the abutment so the same record drives forward
    routing (server -> client) and reverse routing (client -> server).
    ``server_monitor_*`` are absolute OS-pixel bounds, included so the
    client can land its cursor at an absolute server pixel without
    re-syncing the server's monitor list.
    """

    server_monitor_id: int
    server_edge: Edge
    server_axis_start: float
    server_axis_end: float
    server_monitor_min_x: int
    server_monitor_min_y: int
    server_monitor_max_x: int
    server_monitor_max_y: int
    client_monitor_id: int
    client_edge: Edge
    client_axis_start: float
    client_axis_end: float

    def contains_server_axis(self, axis_norm: float) -> bool:
        return self.server_axis_start <= axis_norm < self.server_axis_end

    def contains_client_axis(self, axis_norm: float) -> bool:
        return self.client_axis_start <= axis_norm < self.client_axis_end

    def map_server_to_client_axis(self, server_axis_norm: float) -> float:
        """Linear map from server edge axis to client edge axis, clamped to [start, end]."""
        span = self.server_axis_end - self.server_axis_start
        if span <= 0:
            return self.client_axis_start
        local = (server_axis_norm - self.server_axis_start) / span
        local = max(0.0, min(1.0, local))
        return self.client_axis_start + local * (
            self.client_axis_end - self.client_axis_start
        )

    def map_client_to_server_axis(self, client_axis_norm: float) -> float:
        """Reverse of :meth:`map_server_to_client_axis`."""
        span = self.client_axis_end - self.client_axis_start
        if span <= 0:
            return self.server_axis_start
        local = (client_axis_norm - self.client_axis_start) / span
        local = max(0.0, min(1.0, local))
        return self.server_axis_start + local * (
            self.server_axis_end - self.server_axis_start
        )

    def to_dict(self) -> dict:
        return {
            "server_monitor_id": self.server_monitor_id,
            "server_edge": self.server_edge.value,
            "server_axis_start": self.server_axis_start,
            "server_axis_end": self.server_axis_end,
            "server_monitor_min_x": self.server_monitor_min_x,
            "server_monitor_min_y": self.server_monitor_min_y,
            "server_monitor_max_x": self.server_monitor_max_x,
            "server_monitor_max_y": self.server_monitor_max_y,
            "client_monitor_id": self.client_monitor_id,
            "client_edge": self.client_edge.value,
            "client_axis_start": self.client_axis_start,
            "client_axis_end": self.client_axis_end,
        }


def _make_binding(
    s,
    server_edge: Edge,
    s_axis_start_px: float,
    s_axis_end_px: float,
    s_axis_total_px: float,
    c_axis_start_px: float,
    c_axis_end_px: float,
    c_axis_total_px: float,
    client_monitor_id: int,
) -> EdgeBinding:
    return EdgeBinding(
        server_monitor_id=s.monitor_id,
        server_edge=server_edge,
        server_axis_start=s_axis_start_px / s_axis_total_px,
        server_axis_end=s_axis_end_px / s_axis_total_px,
        server_monitor_min_x=s.min_x,
        server_monitor_min_y=s.min_y,
        server_monitor_max_x=s.max_x,
        server_monitor_max_y=s.max_y,
        client_monitor_id=client_monitor_id,
        client_edge=_OPPOSITE_EDGE[server_edge],
        client_axis_start=c_axis_start_px / c_axis_total_px,
        client_axis_end=c_axis_end_px / c_axis_total_px,
    )


def compute_edge_bindings(
    placement: dict,
    server_monitors: "Iterable[MonitorInfo] | list[MonitorInfo]",
) -> list[EdgeBinding]:
    """Enumerate every server-edge zone abutting one client-monitor placement.

    A single placement can produce multiple bindings — e.g. straddling
    a corner between two adjacent server monitors yields one per
    touched edge.
    """
    px = int(placement.get("workspace_x", 0))
    py = int(placement.get("workspace_y", 0))
    pw = int(placement.get("width", 0))
    ph = int(placement.get("height", 0))
    if pw <= 0 or ph <= 0:
        return []
    client_monitor_id = int(placement.get("client_monitor_id", 0))
    out: list[EdgeBinding] = []

    for s in server_monitors:
        sw = s.max_x - s.min_x
        sh = s.max_y - s.min_y
        if sw <= 0 or sh <= 0:
            continue

        # placement.left == server.right
        if abs(px - s.max_x) <= _ABUTMENT_TOLERANCE_PX:
            y_start = max(py, s.min_y)
            y_end = min(py + ph, s.max_y)
            if y_end > y_start:
                out.append(
                    _make_binding(
                        s,
                        Edge.RIGHT,
                        s_axis_start_px=y_start - s.min_y,
                        s_axis_end_px=y_end - s.min_y,
                        s_axis_total_px=sh,
                        c_axis_start_px=y_start - py,
                        c_axis_end_px=y_end - py,
                        c_axis_total_px=ph,
                        client_monitor_id=client_monitor_id,
                    )
                )

        # placement.right == server.left
        if abs((px + pw) - s.min_x) <= _ABUTMENT_TOLERANCE_PX:
            y_start = max(py, s.min_y)
            y_end = min(py + ph, s.max_y)
            if y_end > y_start:
                out.append(
                    _make_binding(
                        s,
                        Edge.LEFT,
                        s_axis_start_px=y_start - s.min_y,
                        s_axis_end_px=y_end - s.min_y,
                        s_axis_total_px=sh,
                        c_axis_start_px=y_start - py,
                        c_axis_end_px=y_end - py,
                        c_axis_total_px=ph,
                        client_monitor_id=client_monitor_id,
                    )
                )

        # placement.bottom == server.top
        if abs((py + ph) - s.min_y) <= _ABUTMENT_TOLERANCE_PX:
            x_start = max(px, s.min_x)
            x_end = min(px + pw, s.max_x)
            if x_end > x_start:
                out.append(
                    _make_binding(
                        s,
                        Edge.TOP,
                        s_axis_start_px=x_start - s.min_x,
                        s_axis_end_px=x_end - s.min_x,
                        s_axis_total_px=sw,
                        c_axis_start_px=x_start - px,
                        c_axis_end_px=x_end - px,
                        c_axis_total_px=pw,
                        client_monitor_id=client_monitor_id,
                    )
                )

        # placement.top == server.bottom
        if abs(py - s.max_y) <= _ABUTMENT_TOLERANCE_PX:
            x_start = max(px, s.min_x)
            x_end = min(px + pw, s.max_x)
            if x_end > x_start:
                out.append(
                    _make_binding(
                        s,
                        Edge.BOTTOM,
                        s_axis_start_px=x_start - s.min_x,
                        s_axis_end_px=x_end - s.min_x,
                        s_axis_total_px=sw,
                        c_axis_start_px=x_start - px,
                        c_axis_end_px=x_end - px,
                        c_axis_total_px=pw,
                        client_monitor_id=client_monitor_id,
                    )
                )

    return out


def compute_intra_client_bindings(
    placements: "Iterable[dict] | list[dict]",
) -> list[dict]:
    """Cross-monitor warp bindings within a single client.

    For every ordered pair of placements with distinct monitor ids,
    emit one entry per edge abutment. The client uses these to enforce
    the workspace topology over its OS-level monitor adjacency: an
    unbound OS-driven transition is reverted; a bound transition is
    honoured via explicit warp.
    """
    placements = list(placements)
    if len(placements) < 2:
        return []

    out: list[dict] = []
    for p in placements:
        try:
            px = int(p["workspace_x"])
            py = int(p["workspace_y"])
            pw = int(p["width"])
            ph = int(p["height"])
            p_id = int(p["client_monitor_id"])
        except (KeyError, TypeError, ValueError):
            continue
        if pw <= 0 or ph <= 0:
            continue
        for q in placements:
            try:
                q_id = int(q["client_monitor_id"])
            except (KeyError, TypeError, ValueError):
                continue
            if q_id == p_id:
                continue
            try:
                qx = int(q["workspace_x"])
                qy = int(q["workspace_y"])
                qw = int(q["width"])
                qh = int(q["height"])
            except (KeyError, TypeError, ValueError):
                continue
            if qw <= 0 or qh <= 0:
                continue

            # p.RIGHT abuts q.LEFT
            if abs((px + pw) - qx) <= _ABUTMENT_TOLERANCE_PX:
                y_start = max(py, qy)
                y_end = min(py + ph, qy + qh)
                if y_end > y_start:
                    out.append(
                        {
                            "src_monitor_id": p_id,
                            "src_edge": Edge.RIGHT.value,
                            "src_axis_start": (y_start - py) / ph,
                            "src_axis_end": (y_end - py) / ph,
                            "dst_monitor_id": q_id,
                            "dst_edge": Edge.LEFT.value,
                            "dst_axis_start": (y_start - qy) / qh,
                            "dst_axis_end": (y_end - qy) / qh,
                            "dst_monitor_min_x": qx,
                            "dst_monitor_min_y": qy,
                            "dst_monitor_max_x": qx + qw,
                            "dst_monitor_max_y": qy + qh,
                        }
                    )
            # p.LEFT abuts q.RIGHT
            if abs(px - (qx + qw)) <= _ABUTMENT_TOLERANCE_PX:
                y_start = max(py, qy)
                y_end = min(py + ph, qy + qh)
                if y_end > y_start:
                    out.append(
                        {
                            "src_monitor_id": p_id,
                            "src_edge": Edge.LEFT.value,
                            "src_axis_start": (y_start - py) / ph,
                            "src_axis_end": (y_end - py) / ph,
                            "dst_monitor_id": q_id,
                            "dst_edge": Edge.RIGHT.value,
                            "dst_axis_start": (y_start - qy) / qh,
                            "dst_axis_end": (y_end - qy) / qh,
                            "dst_monitor_min_x": qx,
                            "dst_monitor_min_y": qy,
                            "dst_monitor_max_x": qx + qw,
                            "dst_monitor_max_y": qy + qh,
                        }
                    )
            # p.BOTTOM abuts q.TOP
            if abs((py + ph) - qy) <= _ABUTMENT_TOLERANCE_PX:
                x_start = max(px, qx)
                x_end = min(px + pw, qx + qw)
                if x_end > x_start:
                    out.append(
                        {
                            "src_monitor_id": p_id,
                            "src_edge": Edge.BOTTOM.value,
                            "src_axis_start": (x_start - px) / pw,
                            "src_axis_end": (x_end - px) / pw,
                            "dst_monitor_id": q_id,
                            "dst_edge": Edge.TOP.value,
                            "dst_axis_start": (x_start - qx) / qw,
                            "dst_axis_end": (x_end - qx) / qw,
                            "dst_monitor_min_x": qx,
                            "dst_monitor_min_y": qy,
                            "dst_monitor_max_x": qx + qw,
                            "dst_monitor_max_y": qy + qh,
                        }
                    )
            # p.TOP abuts q.BOTTOM
            if abs(py - (qy + qh)) <= _ABUTMENT_TOLERANCE_PX:
                x_start = max(px, qx)
                x_end = min(px + pw, qx + qw)
                if x_end > x_start:
                    out.append(
                        {
                            "src_monitor_id": p_id,
                            "src_edge": Edge.TOP.value,
                            "src_axis_start": (x_start - px) / pw,
                            "src_axis_end": (x_end - px) / pw,
                            "dst_monitor_id": q_id,
                            "dst_edge": Edge.BOTTOM.value,
                            "dst_axis_start": (x_start - qx) / qw,
                            "dst_axis_end": (x_end - qx) / qw,
                            "dst_monitor_min_x": qx,
                            "dst_monitor_min_y": qy,
                            "dst_monitor_max_x": qx + qw,
                            "dst_monitor_max_y": qy + qh,
                        }
                    )

    return out
