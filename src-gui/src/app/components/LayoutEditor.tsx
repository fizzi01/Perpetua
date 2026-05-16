/*
 * Perpetua - open-source and cross-platform KVM software.
 * Copyright (c) 2026 Federico Izzi.
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program.  If not, see <https://www.gnu.org/licenses/>.
 */

import {useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState} from "react";
import type {
    MonitorInfo,
    MonitorPlacement,
} from "../api/Interface";
import {
    isAdjacentToAny,
    monitorAsRect,
    placementAsRect,
    rectsOverlap,
    snapRect,
    suggestInitialPlacement,
    validatePlacements,
    workspaceBounds,
} from "../commons/layout";

// Client summary the editor needs. ``monitors`` is the source of truth
// for what can be placed (each MonitorInfo becomes a draggable box).
export interface LayoutEditorClient {
    uid: string;
    name: string;
    monitors?: MonitorInfo[];
    color?: string;
}

export interface LayoutEditorProps {
    serverMonitors: MonitorInfo[];
    clients: LayoutEditorClient[];
    placements: MonitorPlacement[];
    onChange: (placements: MonitorPlacement[]) => void;
    onValidityChange?: (ok: boolean, errors: string[]) => void;
    height?: number;
}

const FALLBACK_PALETTE = [
    "#7c3aed", "#0ea5e9", "#22c55e", "#f97316",
    "#ec4899", "#14b8a6", "#eab308", "#a855f7",
];

function colorFor(uid: string, idx: number): string {
    if (!uid) return FALLBACK_PALETTE[idx % FALLBACK_PALETTE.length];
    let h = 0;
    for (let i = 0; i < uid.length; i++) {
        h = (h * 31 + uid.charCodeAt(i)) >>> 0;
    }
    return FALLBACK_PALETTE[h % FALLBACK_PALETTE.length];
}

interface ViewMetrics {
    scale: number;
    offsetX: number;
    offsetY: number;
}

function computeViewMetrics(
    bounds: {x: number; y: number; width: number; height: number},
    canvasW: number,
    canvasH: number,
    padding: number,
): ViewMetrics {
    const usableW = Math.max(1, canvasW - padding * 2);
    const usableH = Math.max(1, canvasH - padding * 2);
    const w = Math.max(1, bounds.width);
    const h = Math.max(1, bounds.height);
    const scale = Math.min(usableW / w, usableH / h);
    // Center the workspace inside the canvas.
    const renderedW = w * scale;
    const renderedH = h * scale;
    return {
        scale,
        offsetX: (canvasW - renderedW) / 2 - bounds.x * scale,
        offsetY: (canvasH - renderedH) / 2 - bounds.y * scale,
    };
}

function workspaceToCanvas(
    x: number,
    y: number,
    m: ViewMetrics,
): {x: number; y: number} {
    return {x: x * m.scale + m.offsetX, y: y * m.scale + m.offsetY};
}

function canvasToWorkspace(
    x: number,
    y: number,
    m: ViewMetrics,
): {x: number; y: number} {
    return {x: (x - m.offsetX) / m.scale, y: (y - m.offsetY) / m.scale};
}

interface DragState {
    placementIdx: number;
    // Pointer offset (in workspace coords) from the placement's top-left
    // corner at drag start, so the box follows the cursor cleanly.
    grabDx: number;
    grabDy: number;
}

const SNAP_THRESHOLD_PX = 12;

// State used while the user is dragging an unplaced client monitor from
// the sidebar onto the canvas. Pointer-based instead of HTML5 drag
// because the latter is unreliable inside Tauri's WebView on some
// platforms (drop events occasionally don't fire on macOS WKWebView).
interface PendingPlacement {
    clientUid: string;
    clientName: string;
    clientMonitorId: number;
    width: number;
    height: number;
    color: string;
    // Live pointer position in viewport coords for the ghost overlay.
    pointerX: number;
    pointerY: number;
}

export function LayoutEditor({
    serverMonitors,
    clients,
    placements,
    onChange,
    onValidityChange,
    height = 360,
}: LayoutEditorProps) {
    const canvasRef = useRef<HTMLDivElement | null>(null);
    const [canvasSize, setCanvasSize] = useState({width: 600, height});
    const [drag, setDrag] = useState<DragState | null>(null);
    const [pendingNew, setPendingNew] = useState<PendingPlacement | null>(null);

    useLayoutEffect(() => {
        if (!canvasRef.current) return;
        const ro = new ResizeObserver((entries) => {
            for (const e of entries) {
                const w = e.contentRect.width;
                const h = e.contentRect.height;
                setCanvasSize((prev) => {
                    if (Math.abs(prev.width - w) < 0.5 && Math.abs(prev.height - h) < 0.5) {
                        return prev;
                    }
                    return {width: w, height: h};
                });
            }
        });
        ro.observe(canvasRef.current);
        return () => ro.disconnect();
    }, []);

    // Compute the workspace bbox so the view always fits all monitors.
    const bounds = useMemo(
        () => workspaceBounds(serverMonitors, placements),
        [serverMonitors, placements],
    );

    const metrics = useMemo(
        () => computeViewMetrics(bounds, canvasSize.width, canvasSize.height, 32),
        [bounds, canvasSize.width, canvasSize.height],
    );

    const validation = useMemo(
        () => validatePlacements(serverMonitors, placements),
        [serverMonitors, placements],
    );

    useEffect(() => {
        onValidityChange?.(validation.ok, validation.errors);
    }, [validation.ok, validation.errors, onValidityChange]);

    const clientColors = useMemo(() => {
        const m: Record<string, string> = {};
        clients.forEach((c, i) => (m[c.uid] = c.color || colorFor(c.uid, i)));
        return m;
    }, [clients]);

    const clientByUid = useMemo(() => {
        const m: Record<string, LayoutEditorClient> = {};
        clients.forEach((c) => (m[c.uid] = c));
        return m;
    }, [clients]);

    // Monitors of every client that haven't been placed yet — these
    // show up in the sidebar as drag sources.
    const unplaced = useMemo(() => {
        const placed = new Set(
            placements.map((p) => `${p.client_uid}:${p.client_monitor_id}`),
        );
        const out: Array<{
            clientUid: string;
            clientName: string;
            monitor: MonitorInfo;
        }> = [];
        for (const c of clients) {
            for (const m of c.monitors ?? []) {
                if (!placed.has(`${c.uid}:${m.monitor_id}`)) {
                    out.push({clientUid: c.uid, clientName: c.name, monitor: m});
                }
            }
        }
        return out;
    }, [clients, placements]);

    // ------------------------------------------------------------------
    // Drag of an existing placement (workspace box).
    // ------------------------------------------------------------------

    function onPlacementPointerDown(e: React.PointerEvent, idx: number) {
        e.preventDefault();
        e.stopPropagation();
        if (!canvasRef.current) return;
        const rect = canvasRef.current.getBoundingClientRect();
        const ws = canvasToWorkspace(
            e.clientX - rect.left,
            e.clientY - rect.top,
            metrics,
        );
        const p = placements[idx];
        setDrag({
            placementIdx: idx,
            grabDx: ws.x - p.workspace_x,
            grabDy: ws.y - p.workspace_y,
        });
        (e.target as Element).setPointerCapture?.(e.pointerId);
    }

    const onDragMove = useCallback((ev: PointerEvent) => {
        if (!drag || !canvasRef.current) return;
        const rect = canvasRef.current.getBoundingClientRect();
        const ws = canvasToWorkspace(
            ev.clientX - rect.left,
            ev.clientY - rect.top,
            metrics,
        );
        const target = placements[drag.placementIdx];
        if (!target) return;

        const candidate = {
            x: ws.x - drag.grabDx,
            y: ws.y - drag.grabDy,
            width: target.width,
            height: target.height,
        };

        // Snap to neighbouring monitors (server + other placements).
        const others = [
            ...serverMonitors.map(monitorAsRect),
            ...placements
                .filter((_, i) => i !== drag.placementIdx)
                .map(placementAsRect),
        ];
        // Convert snap threshold from canvas px to workspace px.
        const snapped = snapRect(candidate, others, SNAP_THRESHOLD_PX / metrics.scale);
        const snappedRect = {...candidate, x: snapped.x, y: snapped.y};

        // Adjacency clamp: the box must always touch at least one
        // server monitor or another placed client. Without this rule
        // the user can drag the box into empty space arbitrarily far,
        // which doesn't correspond to any meaningful spatial
        // arrangement (no neighbour means no crossing). If the move
        // would break adjacency, we simply don't commit it — the box
        // stays at its last valid position and the cursor leads ahead
        // of it until the user comes back near a neighbour.
        if (!isAdjacentToAny(snappedRect, others)) return;
        // Reject moves that produce overlaps too: keeping the previous
        // valid position is less confusing than silently flagging an
        // error every time the cursor crosses another monitor.
        if (others.some((r) => rectsOverlap(snappedRect, r))) return;

        const next = placements.slice();
        next[drag.placementIdx] = {
            ...target,
            workspace_x: snapped.x,
            workspace_y: snapped.y,
        };
        onChange(next);
    }, [drag, metrics, placements, serverMonitors, onChange]);

    const onDragEnd = useCallback(() => {
        setDrag(null);
    }, []);

    useEffect(() => {
        if (!drag) return;
        window.addEventListener("pointermove", onDragMove);
        window.addEventListener("pointerup", onDragEnd);
        window.addEventListener("pointercancel", onDragEnd);
        return () => {
            window.removeEventListener("pointermove", onDragMove);
            window.removeEventListener("pointerup", onDragEnd);
            window.removeEventListener("pointercancel", onDragEnd);
        };
    }, [drag, onDragMove, onDragEnd]);

    // ------------------------------------------------------------------
    // Pointer-based drag-to-place flow for the sidebar.
    //
    // HTML5 drag-and-drop turned out to be flaky inside Tauri's WebView
    // (drop events occasionally don't fire on macOS WKWebView), which
    // is what the user was seeing as "small box that doesn't even work".
    // Pointer events behave identically on every platform.
    // ------------------------------------------------------------------

    function onSidebarPointerDown(
        e: React.PointerEvent,
        clientUid: string,
        clientName: string,
        monitor: MonitorInfo,
    ) {
        e.preventDefault();
        e.stopPropagation();
        const width = monitor.max_x - monitor.min_x;
        const heightPx = monitor.max_y - monitor.min_y;
        setPendingNew({
            clientUid,
            clientName,
            clientMonitorId: monitor.monitor_id,
            width,
            height: heightPx,
            color: clientColors[clientUid] || "#7c3aed",
            pointerX: e.clientX,
            pointerY: e.clientY,
        });
        (e.target as Element).setPointerCapture?.(e.pointerId);
    }

    const onPendingMove = useCallback((ev: PointerEvent) => {
        setPendingNew((prev) =>
            prev ? {...prev, pointerX: ev.clientX, pointerY: ev.clientY} : prev,
        );
    }, []);

    const onPendingUp = useCallback((ev: PointerEvent) => {
        setPendingNew((prev) => {
            if (!prev || !canvasRef.current) return null;
            const rect = canvasRef.current.getBoundingClientRect();
            const inCanvas =
                ev.clientX >= rect.left
                && ev.clientX <= rect.right
                && ev.clientY >= rect.top
                && ev.clientY <= rect.bottom;
            if (!inCanvas) return null;

            const ws = canvasToWorkspace(
                ev.clientX - rect.left,
                ev.clientY - rect.top,
                metrics,
            );
            const others = [
                ...serverMonitors.map(monitorAsRect),
                ...placements.map(placementAsRect),
            ];

            // Find the best landing spot near the drop point that is
            // adjacent to a server (or another placed client) and
            // doesn't overlap anything. Strategy:
            //   1. snap the cursor-centered candidate;
            //   2. if it lands adjacent + non-overlap, accept;
            //   3. otherwise pick the closest valid slot among the
            //      four flush-to-edge positions around each existing
            //      neighbour (right-of, left-of, below, above).
            const cursorCandidate = snapRect(
                {
                    x: ws.x - prev.width / 2,
                    y: ws.y - prev.height / 2,
                    width: prev.width,
                    height: prev.height,
                },
                others,
                SNAP_THRESHOLD_PX / metrics.scale,
            );
            const cursorRect = {
                ...cursorCandidate,
                width: prev.width,
                height: prev.height,
            };

            let chosen: {x: number; y: number} | null = null;
            if (
                isAdjacentToAny(cursorRect, others)
                && !others.some((r) => rectsOverlap(cursorRect, r))
            ) {
                chosen = {x: cursorCandidate.x, y: cursorCandidate.y};
            } else {
                // Search every flush-to-edge slot around each existing
                // monitor and pick the one closest to the drop point.
                let bestDist = Infinity;
                for (const r of others) {
                    const candidates = [
                        // right of `r`
                        {x: r.x + r.width, y: r.y},
                        // left of `r`
                        {x: r.x - prev.width, y: r.y},
                        // below `r`
                        {x: r.x, y: r.y + r.height},
                        // above `r`
                        {x: r.x, y: r.y - prev.height},
                    ];
                    for (const c of candidates) {
                        const test = {
                            x: c.x,
                            y: c.y,
                            width: prev.width,
                            height: prev.height,
                        };
                        if (others.some((o) => rectsOverlap(test, o))) continue;
                        const dx = c.x + prev.width / 2 - ws.x;
                        const dy = c.y + prev.height / 2 - ws.y;
                        const d2 = dx * dx + dy * dy;
                        if (d2 < bestDist) {
                            bestDist = d2;
                            chosen = {x: c.x, y: c.y};
                        }
                    }
                }
                if (!chosen) {
                    const fallback = suggestInitialPlacement(
                        serverMonitors,
                        placements,
                    );
                    chosen = {x: fallback.x, y: fallback.y};
                }
            }

            const newPlacement: MonitorPlacement = {
                client_uid: prev.clientUid,
                client_monitor_id: prev.clientMonitorId,
                workspace_x: chosen.x,
                workspace_y: chosen.y,
                width: prev.width,
                height: prev.height,
            };
            onChange([...placements, newPlacement]);
            return null;
        });
    }, [metrics, placements, serverMonitors, onChange]);

    useEffect(() => {
        if (!pendingNew) return;
        window.addEventListener("pointermove", onPendingMove);
        window.addEventListener("pointerup", onPendingUp);
        window.addEventListener("pointercancel", () => setPendingNew(null));
        return () => {
            window.removeEventListener("pointermove", onPendingMove);
            window.removeEventListener("pointerup", onPendingUp);
        };
    }, [pendingNew, onPendingMove, onPendingUp]);

    function removePlacement(idx: number) {
        const next = placements.slice();
        next.splice(idx, 1);
        onChange(next);
    }

    // ------------------------------------------------------------------
    // Render
    // ------------------------------------------------------------------

    function renderServerMonitor(m: MonitorInfo, idx: number) {
        const tl = workspaceToCanvas(m.min_x, m.min_y, metrics);
        const br = workspaceToCanvas(m.max_x, m.max_y, metrics);
        const w = br.x - tl.x;
        const h = br.y - tl.y;
        return (
            <div
                key={`srv-${m.monitor_id}-${idx}`}
                style={{
                    position: "absolute",
                    left: tl.x,
                    top: tl.y,
                    width: w,
                    height: h,
                    borderRadius: 8,
                    border: "2px solid var(--app-primary)",
                    background: m.is_primary
                        ? "color-mix(in srgb, var(--app-card-bg) 18%, transparent)"
                        : "color-mix(in srgb, var(--app-card-bg) 8%, transparent)",
                    color: "var(--app-primary)",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    fontSize: 12,
                    fontWeight: 600,
                    userSelect: "none",
                    pointerEvents: "none",
                }}
                title={`Server monitor #${m.monitor_id}${m.is_primary ? " (primary)" : ""} · ${m.max_x - m.min_x}×${m.max_y - m.min_y}`}
            >
                <div style={{textAlign: "center", lineHeight: 1.2}}>
                    <div style={{fontSize: 10, opacity: 0.7, fontWeight: 500}}>SERVER</div>
                    <div>#{m.monitor_id}</div>
                </div>
            </div>
        );
    }

    function renderPlacement(p: MonitorPlacement, idx: number) {
        const tl = workspaceToCanvas(p.workspace_x, p.workspace_y, metrics);
        const br = workspaceToCanvas(p.workspace_x + p.width, p.workspace_y + p.height, metrics);
        const w = br.x - tl.x;
        const h = br.y - tl.y;
        const client = clientByUid[p.client_uid];
        const color = clientColors[p.client_uid] || "#7c3aed";
        const isBad = validation.overlappingIndices.has(idx);
        const isDragging = drag?.placementIdx === idx;
        return (
            <div
                key={`p-${p.client_uid}-${p.client_monitor_id}-${idx}`}
                onPointerDown={(e) => onPlacementPointerDown(e, idx)}
                style={{
                    position: "absolute",
                    left: tl.x,
                    top: tl.y,
                    width: w,
                    height: h,
                    borderRadius: 8,
                    border: isBad
                        ? "2px solid #ef4444"
                        : `2px solid ${color}`,
                    background: `${color}33`,
                    color,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    fontSize: 12,
                    fontWeight: 600,
                    userSelect: "none",
                    cursor: isDragging ? "grabbing" : "grab",
                    boxShadow: isDragging
                        ? "0 4px 12px rgba(0,0,0,0.25)"
                        : "none",
                    zIndex: isDragging ? 10 : 1,
                    touchAction: "none",
                }}
                title={
                    isBad
                        ? `${client?.name || p.client_uid} · monitor #${p.client_monitor_id} · overlaps with another monitor — drag to a free area`
                        : `${client?.name || p.client_uid} · monitor #${p.client_monitor_id} · ${p.width}×${p.height}`
                }
            >
                <div style={{textAlign: "center", pointerEvents: "none", lineHeight: 1.2}}>
                    <div style={{fontSize: 10, opacity: 0.85, fontWeight: 500}}>
                        {client?.name || p.client_uid}
                    </div>
                    <div style={{fontSize: 11}}>#{p.client_monitor_id}</div>
                </div>
                {isBad && (
                    <div
                        style={{
                            position: "absolute",
                            top: 3,
                            left: 3,
                            width: 16,
                            height: 16,
                            borderRadius: "50%",
                            backgroundColor: "#ef4444",
                            color: "white",
                            fontSize: 11,
                            fontWeight: 700,
                            lineHeight: "16px",
                            textAlign: "center",
                            pointerEvents: "none",
                        }}
                    >
                        !
                    </div>
                )}
                <button
                    onClick={(e) => {
                        e.stopPropagation();
                        removePlacement(idx);
                    }}
                    title="Remove from workspace"
                    style={{
                        position: "absolute",
                        top: 3,
                        right: 3,
                        width: 16,
                        height: 16,
                        borderRadius: "50%",
                        border: "none",
                        backgroundColor: "rgba(0,0,0,0.55)",
                        color: "white",
                        fontSize: 10,
                        lineHeight: "16px",
                        padding: 0,
                        cursor: "pointer",
                    }}
                >
                    ×
                </button>
            </div>
        );
    }

    return (
        <div style={{display: "flex", gap: 8, alignItems: "stretch", width: "100%", minHeight: 0}}>
            <div
                style={{
                    flex: "0 0 150px",
                    display: "flex",
                    flexDirection: "column",
                    gap: 6,
                    minHeight: 0,
                }}
            >
                {unplaced.length === 0 ? (
                    <div style={{fontSize: 11, opacity: 0.55, margin: "auto", textAlign: "center"}}>
                        All monitors placed
                    </div>
                ) : (
                    <div style={{display: "flex", flexDirection: "column", gap: 5, overflowY: "auto"}}>
                        {unplaced.map((u, i) => (
                            <div
                                key={`${u.clientUid}:${u.monitor.monitor_id}:${i}`}
                                onPointerDown={(e) =>
                                    onSidebarPointerDown(
                                        e,
                                        u.clientUid,
                                        u.clientName,
                                        u.monitor,
                                    )
                                }
                                style={{
                                    padding: "5px 8px",
                                    borderRadius: 5,
                                    backgroundColor: clientColors[u.clientUid],
                                    color: "white",
                                    cursor: "grab",
                                    fontSize: 11,
                                    userSelect: "none",
                                    touchAction: "none",
                                    lineHeight: 1.3,
                                }}
                                title={`${u.clientName} · monitor #${u.monitor.monitor_id} · ${u.monitor.max_x - u.monitor.min_x}×${u.monitor.max_y - u.monitor.min_y}`}
                            >
                                <div style={{fontWeight: 600}}>{u.clientName}</div>
                                <div style={{fontSize: 10, opacity: 0.85}}>
                                    #{u.monitor.monitor_id}
                                </div>
                            </div>
                        ))}
                    </div>
                )}
            </div>

            <div
                ref={canvasRef}
                style={{
                    flex: 1,
                    minHeight: height,
                    height,
                    position: "relative",
                    borderRadius: 8,
                    background:
                        "repeating-linear-gradient(45deg, rgba(120,120,120,0.04) 0 10px, transparent 10px 20px)",
                    border: "1px solid rgba(120,120,120,0.25)",
                    overflow: "hidden",
                }}
            >
                {serverMonitors.length === 0 && (
                    <div
                        style={{
                            position: "absolute",
                            inset: 0,
                            display: "flex",
                            alignItems: "center",
                            justifyContent: "center",
                            fontSize: 12,
                            opacity: 0.6,
                        }}
                    >
                        No server monitors available.
                    </div>
                )}
                {serverMonitors.map(renderServerMonitor)}
                {placements.map(renderPlacement)}
                {pendingNew && canvasRef.current && (() => {
                    const rect = canvasRef.current.getBoundingClientRect();
                    const inside =
                        pendingNew.pointerX >= rect.left
                        && pendingNew.pointerX <= rect.right
                        && pendingNew.pointerY >= rect.top
                        && pendingNew.pointerY <= rect.bottom;
                    if (!inside) return null;
                    const ghostW = pendingNew.width * metrics.scale;
                    const ghostH = pendingNew.height * metrics.scale;
                    return (
                        <div
                            style={{
                                position: "absolute",
                                left: pendingNew.pointerX - rect.left - ghostW / 2,
                                top: pendingNew.pointerY - rect.top - ghostH / 2,
                                width: ghostW,
                                height: ghostH,
                                borderRadius: 8,
                                border: `2px dashed ${pendingNew.color}`,
                                background: `${pendingNew.color}33`,
                                pointerEvents: "none",
                                opacity: 0.85,
                            }}
                        />
                    );
                })()}
                {!validation.ok && (
                    <div
                        title={validation.errors.slice(0, 4).join("\n")}
                        style={{
                            position: "absolute",
                            top: 6,
                            left: 6,
                            display: "flex",
                            alignItems: "center",
                            gap: 6,
                            padding: "3px 8px",
                            borderRadius: 999,
                            fontSize: 11,
                            fontWeight: 600,
                            color: "white",
                            backgroundColor: "rgba(239, 68, 68, 0.92)",
                            boxShadow: "0 1px 4px rgba(0,0,0,0.25)",
                            pointerEvents: "auto",
                        }}
                    >
                        <span style={{fontSize: 12, lineHeight: 1}}>⚠</span>
                        <span>
                            {validation.errors.length} overlap
                            {validation.errors.length === 1 ? "" : "s"}
                        </span>
                    </div>
                )}
            </div>
        </div>
    );
}
