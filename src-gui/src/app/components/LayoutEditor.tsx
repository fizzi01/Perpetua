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

import { Monitor, GripVertical } from "lucide-react";

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
    // Spotlight a client's monitors in the sidebar on mount (approve/add auto-open flow).
    preselectClientUid?: string;
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
    grabDx: number;
    grabDy: number;
    // Original position captured at pointerdown - used to revert if no valid landing is found.
    originX: number;
    originY: number;
}

const SNAP_THRESHOLD_PX = 12;

// Pointer-based drag from sidebar; HTML5 DnD drop events are unreliable in Tauri's WebView (macOS WKWebView).
interface PendingPlacement {
    clientUid: string;
    clientName: string;
    clientMonitorId: number;
    width: number;
    height: number;
    color: string;
    pointerX: number;
    pointerY: number;
}

export function LayoutEditor({
    serverMonitors,
    clients,
    placements,
    preselectClientUid,
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
            originX: p.workspace_x,
            originY: p.workspace_y,
        });
        (e.target as Element).setPointerCapture?.(e.pointerId);
    }

    // Validation isn't enforced during the move (just visual feedback); see onDragEnd for snap-on-release.
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

        const others = [
            ...serverMonitors.map(monitorAsRect),
            ...placements
                .filter((_, i) => i !== drag.placementIdx)
                .map(placementAsRect),
        ];
        const snapped = snapRect(candidate, others, SNAP_THRESHOLD_PX / metrics.scale);

        const next = placements.slice();
        next[drag.placementIdx] = {
            ...target,
            workspace_x: snapped.x,
            workspace_y: snapped.y,
        };
        onChange(next);
    }, [drag, metrics, placements, serverMonitors, onChange]);

    // On release: if invalid, snap to the closest valid flush-to-edge slot around existing rects; else revert to origin.
    const onDragEnd = useCallback(() => {
        setDrag((d) => {
            if (!d) return null;
            const target = placements[d.placementIdx];
            if (!target) return null;

            const others = [
                ...serverMonitors.map(monitorAsRect),
                ...placements
                    .filter((_, i) => i !== d.placementIdx)
                    .map(placementAsRect),
            ];
            const candidate = {
                x: target.workspace_x,
                y: target.workspace_y,
                width: target.width,
                height: target.height,
            };

            const isValid = (r: typeof candidate) =>
                isAdjacentToAny(r, others)
                && !others.some((o) => rectsOverlap(r, o));

            if (!isValid(candidate)) {
                let bestDist = Infinity;
                let bestX = d.originX;
                let bestY = d.originY;
                const cx = candidate.x + candidate.width / 2;
                const cy = candidate.y + candidate.height / 2;
                for (const r of others) {
                    const slots = [
                        {x: r.x + r.width, y: r.y},
                        {x: r.x - candidate.width, y: r.y},
                        {x: r.x, y: r.y + r.height},
                        {x: r.x, y: r.y - candidate.height},
                    ];
                    for (const s of slots) {
                        const test = {
                            x: s.x,
                            y: s.y,
                            width: candidate.width,
                            height: candidate.height,
                        };
                        if (!isValid(test)) continue;
                        const dx = s.x + candidate.width / 2 - cx;
                        const dy = s.y + candidate.height / 2 - cy;
                        const d2 = dx * dx + dy * dy;
                        if (d2 < bestDist) {
                            bestDist = d2;
                            bestX = s.x;
                            bestY = s.y;
                        }
                    }
                }
                const next = placements.slice();
                next[d.placementIdx] = {
                    ...target,
                    workspace_x: bestX,
                    workspace_y: bestY,
                };
                onChange(next);
            }
            return null;
        });
    }, [placements, serverMonitors, onChange]);

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

            // Try the snapped cursor-centered candidate first, then fall back to the closest valid flush-to-edge slot.
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
                let bestDist = Infinity;
                for (const r of others) {
                    const candidates = [
                        {x: r.x + r.width, y: r.y},
                        {x: r.x - prev.width, y: r.y},
                        {x: r.x, y: r.y + r.height},
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
                        ? `${client?.name || p.client_uid} · monitor #${p.client_monitor_id} · overlaps with another monitor - drag to a free area`
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
        <div className="flex gap-3 items-stretch w-full h-full min-h-0 p-3">
            <div className="w-[150px] shrink-0 flex flex-col gap-2 min-h-0">
                {unplaced.length === 0 ? (
                    <div className="text-[11px] opacity-55 m-auto text-center">
                        All monitors placed
                    </div>
                ) : (
                    <div className="flex flex-col gap-2 overflow-y-auto p-1">
                        <div className="text-[10px] uppercase font-semibold opacity-50 mb-1 px-1 flex items-center justify-between">
                            <span>Unplaced</span>
                            <span className="text-[9px] lowercase font-normal opacity-80 flex items-center gap-1">
                                <GripVertical size={10} /> drag to place
                            </span>
                        </div>
                        {unplaced.map((u, i) => {
                            const highlighted = !!preselectClientUid
                                && u.clientUid === preselectClientUid;
                            const isBeingDragged = pendingNew?.clientUid === u.clientUid && pendingNew?.clientMonitorId === u.monitor.monitor_id;
                            return (
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
                                    className={`px-2 py-2 rounded-lg text-white text-[11px] select-none leading-snug touch-none shadow-sm transition-all flex items-center gap-1.5 group hover:brightness-110 ${isBeingDragged ? 'cursor-grabbing' : 'cursor-grab'}`}
                                    style={{
                                        backgroundColor: clientColors[u.clientUid],
                                        opacity: isBeingDragged ? 0.4 : 1,
                                        transform: isBeingDragged ? "scale(0.95)" : "scale(1)",
                                        outline: highlighted
                                            ? "2px solid var(--app-primary)"
                                            : "2px solid transparent",
                                        outlineOffset: highlighted ? "2px" : "0px",
                                    }}
                                    title={`${u.clientName} · monitor #${u.monitor.monitor_id} · ${u.monitor.max_x - u.monitor.min_x}×${u.monitor.max_y - u.monitor.min_y}`}
                                >
                                    <GripVertical size={14} className="opacity-50 group-hover:opacity-100 transition-opacity shrink-0" />
                                    <div className="flex-1 min-w-0">
                                        <div className="font-semibold flex items-center gap-1.5 truncate">
                                            <Monitor size={12} className="shrink-0" />
                                            <span className="truncate">{u.clientName}</span>
                                        </div>
                                        <div className="text-[10px] opacity-85">
                                            #{u.monitor.monitor_id}
                                        </div>
                                    </div>
                                </div>
                            );
                        })}
                    </div>
                )}
            </div>

            <div
                ref={canvasRef}
                className="flex-1 relative rounded-xl border overflow-hidden"
                style={{
                    minHeight: height,
                    height,
                    background:
                        "repeating-linear-gradient(45deg, rgba(120,120,120,0.04) 0 10px, transparent 10px 20px)",
                    borderColor: "var(--app-card-border)",
                }}
            >
                {serverMonitors.length === 0 && (
                    <div className="absolute inset-0 flex items-center justify-center text-xs opacity-60">
                        No server monitors available.
                    </div>
                )}
                {serverMonitors.map(renderServerMonitor)}
                {placements.map(renderPlacement)}
                {!validation.ok && (
                    <div
                        title={validation.errors.slice(0, 4).join("\n")}
                        className="absolute top-2.5 left-2.5 flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-semibold text-white shadow-sm pointer-events-auto"
                        style={{
                            backgroundColor: "rgba(239, 68, 68, 0.92)",
                        }}
                    >
                        <span className="text-xs leading-none">⚠</span>
                        <span>
                            {validation.errors.length} overlap
                            {validation.errors.length === 1 ? "" : "s"}
                        </span>
                    </div>
                )}
            </div>
            {pendingNew && (() => {
                const ghostW = pendingNew.width * metrics.scale;
                const ghostH = pendingNew.height * metrics.scale;
                return (
                    <div
                        style={{
                            position: "fixed",
                            left: pendingNew.pointerX - ghostW / 2,
                            top: pendingNew.pointerY - ghostH / 2,
                            width: ghostW,
                            height: ghostH,
                            borderRadius: 8,
                            border: `2px dashed ${pendingNew.color}`,
                            background: `${pendingNew.color}40`,
                            pointerEvents: "none",
                            opacity: 0.9,
                            zIndex: 100,
                            boxShadow: "0 8px 24px rgba(0,0,0,0.15)",
                            display: "flex",
                            alignItems: "center",
                            justifyContent: "center",
                            fontSize: 12,
                            fontWeight: 600,
                            color: pendingNew.color,
                            backdropFilter: "blur(2px)",
                        }}
                    >
                        <div style={{textAlign: "center", lineHeight: 1.2}}>
                            <div style={{fontSize: 10, opacity: 0.85, fontWeight: 500}}>
                                {pendingNew.clientName}
                            </div>
                            <div style={{fontSize: 11}}>#{pendingNew.clientMonitorId}</div>
                        </div>
                    </div>
                );
            })()}
        </div>
    );
}
