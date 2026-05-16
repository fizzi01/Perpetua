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
"""Multi-monitor data model.

instead of treating the virtual desktop as a single bbox,
each connected display is represented by a
:class:`MonitorInfo` carrying enough metadata to:

- detect per-monitor outer edges (a cursor at the right edge of monitor A
  triggers a crossing only if no other monitor abuts A on the right at
  that Y coordinate),
- denormalize incoming cursor positions onto a specific monitor #TODO
- expose a stable monitor identity so future GUI settings can let users
  customise the spatial arrangement (per-edge target client, custom
  scaling factors, alias names, etc.).

:class:`MonitorLayout` is the aggregate view used by the mouse listener
and edge detector. Today it is built automatically from
:meth:`Screen.get_monitors`; later the GUI can override its
``edge_routes`` to implement asymmetric / user-defined arrangements
without touching the input layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Iterable, Optional


@dataclass(frozen=True)
class MonitorInfo:
    """Geometry + metadata for a single connected display.

    Coordinates are in the OS global display coordinate space (origin at
    the primary monitor's top-left on macOS / Windows; the X server root
    origin on Linux/X11).
    """

    monitor_id: int
    min_x: int
    min_y: int
    max_x: int
    max_y: int
    is_primary: bool = False
    name: str = ""
    scaling_factor: float = 1.0

    @property
    def width(self) -> int:
        return self.max_x - self.min_x

    @property
    def height(self) -> int:
        return self.max_y - self.min_y

    @property
    def bbox(self) -> tuple[int, int, int, int]:
        return self.min_x, self.min_y, self.max_x, self.max_y

    def contains(self, x: float, y: float) -> bool:
        """``True`` if ``(x, y)`` falls inside this monitor's bounds."""
        return self.min_x <= x < self.max_x and self.min_y <= y < self.max_y


@dataclass
class MonitorLayout:
    """Aggregate of the connected displays.

    ``monitors`` is the source of truth; ``virtual_bbox`` is a derived
    convenience for cases where a single union rect is enough.

    ``edge_routes`` is the placeholder hook for future user-configurable
    arrangements: it will map ``(monitor_id, edge_name)`` to a routing
    target (typically a :class:`model.client.ScreenPosition`). Today it
    is empty and edge routing falls back to the global
    ``ServerMouseListener._active_screens`` lookup; the field is reserved
    so the data shape is stable for downstream GUIs / config files.
    """

    monitors: tuple[MonitorInfo, ...] = field(default_factory=tuple)
    edge_routes: dict[tuple[int, str], str] = field(default_factory=dict)

    @classmethod
    def from_bboxes(
        cls,
        bboxes: Iterable[tuple[int, int, int, int]],
        primary_index: Optional[int] = None,
    ) -> "MonitorLayout":
        """Build a layout from raw bbox tuples (no scaling/name info)."""
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
        """Union rect of every monitor (degenerate ``(0, 0, 0, 0)`` if
        the layout is empty)."""
        if not self.monitors:
            return 0, 0, 0, 0
        min_x = min(m.min_x for m in self.monitors)
        min_y = min(m.min_y for m in self.monitors)
        max_x = max(m.max_x for m in self.monitors)
        max_y = max(m.max_y for m in self.monitors)
        return min_x, min_y, max_x, max_y

    def find_monitor_at(self, x: float, y: float) -> Optional[MonitorInfo]:
        """Return the monitor containing ``(x, y)``, or ``None`` (dead zone)."""
        for m in self.monitors:
            if m.contains(x, y):
                return m
        return None

    def has_neighbor_left(self, monitor: MonitorInfo, y: float) -> bool:
        """``True`` if another monitor abuts ``monitor`` on its LEFT
        side at the given Y (i.e. moving further left would land on that
        neighbour, not into empty space)."""
        for m in self.monitors:
            if m.monitor_id == monitor.monitor_id:
                continue
            if m.max_x <= monitor.min_x and m.min_y <= y < m.max_y:
                # Allow a small "snap" tolerance so abutting edges count
                # as neighbours even with a 1-2 pixel gap.
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
    """The four sides of a monitor that can host a crossing slot.

    Kept as :class:`StrEnum` so :class:`LayoutSlot` serializes cleanly
    to JSON / msgpack for the handshake payload.
    """

    LEFT = "left"
    RIGHT = "right"
    TOP = "top"
    BOTTOM = "bottom"


@dataclass(frozen=True)
class LayoutSlot:
    """A reservable slice of one server-monitor edge.

    The current 1-client-per-direction model is the trivial case where a
    slot covers the whole edge (``segment_start=0.0, segment_end=1.0``).
    The future GUI will let the user split a single edge across multiple
    clients (top half of monitor 1 right edge -> client A, bottom half
    -> client B), which is why slots carry a normalized segment range
    along the edge's secondary axis (Y for LEFT/RIGHT, X for TOP/BOTTOM).

    Invariant enforced by :class:`LayoutValidator`: no two slots in a
    single layout may overlap. This is the guarantee the runtime needs so
    a cursor reaching ``(monitor_id, edge)`` at a given secondary
    coordinate routes to exactly one client (or none).
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
        """Two slots are disjoint when they cover different monitors,
        different edges, or non-overlapping segments. Touching at a
        single point (``self.end == other.start``) counts as disjoint so
        a clean split at 0.5 is allowed."""
        if self.monitor_id != other.monitor_id or self.edge != other.edge:
            return False
        return not (
            self.segment_end <= other.segment_start
            or other.segment_end <= self.segment_start
        )

    def contains_secondary(self, axis_norm: float) -> bool:
        """``True`` if the given normalized secondary-axis coord (0..1)
        falls inside this slot's segment range."""
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
    """Pairs a :class:`LayoutSlot` (server-side edge slice) with a routing
    target — a client UID, optionally pinned to a specific monitor on
    that client. ``client_monitor_id`` defaults to ``None`` meaning
    "let the client pick its target monitor"; future GUI work will fill
    it in for per-monitor routing."""

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
    """Validates the no-overlap invariant on a list of bindings.

    Kept as a class (rather than a free function) so callers can extend
    it with monitor-existence checks once we wires it into the GUI.
    ``known_monitor_ids`` is optional and only enforced when set.
    """

    known_monitor_ids: Optional[set[int]] = None

    def validate(self, bindings: Iterable[LayoutBinding]) -> tuple[bool, list[str]]:
        """Return ``(ok, errors)``. Enumerates every conflict instead of
        short-circuiting so a future GUI can surface every broken
        constraint in a single pass."""
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
        """Route ``(monitor_id, edge, axis_norm)`` to a single binding.

        The listener knows which monitor's edge was hit and the
        normalized secondary coordinate; this picks the binding owning
        that segment, or ``None`` if unassigned. Invariant: at most one
        binding matches thanks to :meth:`validate`.
        """
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
    """Result of comparing a pre-configured layout against the monitor
    list a client advertised on its latest handshake.

    On reconnection a client may have FEWER monitors than when the
    layout was configured (a display was unplugged, an external dock was
    disconnected, the user changed arrangement). Existing bindings that
    reference monitors no longer present need to be either dropped or
    surfaced for user resolution; bindings that still match are kept
    verbatim so the rest of the system keeps working.

    Fields:
        kept: bindings whose ``client_monitor_id`` either is ``None``
            (target was implicit) or points to a monitor still present.
        dropped: bindings that referenced a now-missing client monitor.
            Caller decides whether to fall back to "any monitor" routing
            or prompt the user to re-bind.
        missing_monitor_ids: set of monitor ids that the layout
            references but the client no longer advertises.
    """

    kept: tuple["LayoutBinding", ...]
    dropped: tuple["LayoutBinding", ...]
    missing_monitor_ids: frozenset[int]

    @property
    def is_clean(self) -> bool:
        """``True`` when every binding is still valid against the
        client's current monitor list."""
        return not self.dropped and not self.missing_monitor_ids


def reconcile_bindings_with_client_monitors(
    bindings: Iterable[LayoutBinding],
    client_uid: str,
    client_monitor_ids: Iterable[int],
) -> LayoutReconciliation:
    """Filter ``bindings`` against the monitors a specific client now has.

    Only bindings targeting ``client_uid`` are considered for dropping —
    bindings for OTHER clients are kept verbatim regardless of this
    client's monitor list. Bindings with ``client_monitor_id=None``
    (implicit "any monitor") survive because the client picks the
    target at landing time.

    Use case: server receives the client's monitor list on reconnect;
    feed it here together with the previously-configured bindings; any
    binding that pinned this client to a no-longer-present monitor
    surfaces in :attr:`LayoutReconciliation.dropped` so the GUI can ask
    the user to re-bind it.
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
