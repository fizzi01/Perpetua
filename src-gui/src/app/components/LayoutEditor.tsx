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
    Edge,
    MonitorInfo,
    MonitorPlacement,
} from "../api/Interface";
import {
    canvasToWorkspace,
    computeViewMetrics,
    monitorAsRect,
    validatePlacements,
    workspaceBounds,
    workspaceToCanvas,
} from "../commons/layout";
import type {Rect} from "../commons/layout";

import { AlertTriangle, GripVertical, Monitor, X } from "lucide-react";

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

interface DragState {
    placementIdx: number;
    grabDx: number;
    grabDy: number;
    // Original position captured at pointerdown; kept for future cancel/revert flows.
    originX: number;
    originY: number;
    attachment: EdgeAttachment | null;
    lastRect: Rect;
    suppressAttachmentUntilTouch: boolean;
}

interface EdgeAttachment {
    serverMonitorId: number;
    edge: Edge;
}

const ATTACH_THRESHOLD_PX = 10;
const DETACH_THRESHOLD_PX = 18;
const TOUCH_THRESHOLD_PX = 1.5;
const ATTACHED_SLIDE_FACTOR = 0.65;

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
    const [, setSelectedPlacementIdx] = useState<number | null>(null);

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

    const validationSummary = useMemo(() => {
        const overlapCount = validation.overlappingIndices.size;
        const detachedCount = validation.notAdjacentToServerIndices.size;

        if (overlapCount > 0 && detachedCount === 0) {
            return `${overlapCount} overlap${overlapCount === 1 ? "" : "s"}`;
        }
        if (detachedCount > 0 && overlapCount === 0) {
            return `${detachedCount} detached monitor${detachedCount === 1 ? "" : "s"}`;
        }
        const total = overlapCount + detachedCount;
        return `${total} layout issue${total === 1 ? "" : "s"}`;
    }, [validation.overlappingIndices, validation.notAdjacentToServerIndices]);

    useEffect(() => {
        onValidityChange?.(validation.ok, validation.errors);
    }, [validation.ok, validation.errors, onValidityChange]);

    useEffect(() => {
        setSelectedPlacementIdx((idx) =>
            idx !== null && idx >= placements.length ? null : idx,
        );
    }, [placements.length]);

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

    const serverRectByMonitorId = useCallback((monitorId: number) => {
        const monitor = serverMonitors.find((m) => m.monitor_id === monitorId);
        return monitor ? monitorAsRect(monitor) : null;
    }, [serverMonitors]);

    const rangesOverlap = (aStart: number, aEnd: number, bStart: number, bEnd: number) =>
        aStart < bEnd && bStart < aEnd;

    const projectToAttachment = (candidate: Rect, serverRect: Rect, edge: Edge): Rect => {
        switch (edge) {
            case "left":
                return {...candidate, x: serverRect.x - candidate.width};
            case "right":
                return {...candidate, x: serverRect.x + serverRect.width};
            case "top":
                return {...candidate, y: serverRect.y - candidate.height};
            case "bottom":
                return {...candidate, y: serverRect.y + serverRect.height};
            default:
                return candidate;
        }
    };

    const outwardDistance = (candidate: Rect, serverRect: Rect, edge: Edge): number => {
        const attached = projectToAttachment(candidate, serverRect, edge);
        switch (edge) {
            case "left":
                return attached.x - candidate.x;
            case "right":
                return candidate.x - attached.x;
            case "top":
                return attached.y - candidate.y;
            case "bottom":
                return candidate.y - attached.y;
            default:
                return 0;
        }
    };

    const findClosestServerAttachment = useCallback((
        candidate: Rect,
        threshold: number,
    ): EdgeAttachment | null => {
        let best: {attachment: EdgeAttachment; distance: number} | null = null;

        for (const serverMonitor of serverMonitors) {
            const serverRect = monitorAsRect(serverMonitor);
            const candidateRight = candidate.x + candidate.width;
            const candidateBottom = candidate.y + candidate.height;
            const serverRight = serverRect.x + serverRect.width;
            const serverBottom = serverRect.y + serverRect.height;

            const candidates: Array<{
                edge: Edge;
                distance: number;
                overlapsParallel: boolean;
            }> = [
                {
                    edge: "left",
                    distance: Math.abs(candidateRight - serverRect.x),
                    overlapsParallel: rangesOverlap(candidate.y, candidateBottom, serverRect.y, serverBottom),
                },
                {
                    edge: "right",
                    distance: Math.abs(candidate.x - serverRight),
                    overlapsParallel: rangesOverlap(candidate.y, candidateBottom, serverRect.y, serverBottom),
                },
                {
                    edge: "top",
                    distance: Math.abs(candidateBottom - serverRect.y),
                    overlapsParallel: rangesOverlap(candidate.x, candidateRight, serverRect.x, serverRight),
                },
                {
                    edge: "bottom",
                    distance: Math.abs(candidate.y - serverBottom),
                    overlapsParallel: rangesOverlap(candidate.x, candidateRight, serverRect.x, serverRight),
                },
            ];

            for (const option of candidates) {
                if (!option.overlapsParallel || option.distance > threshold) continue;
                if (!best || option.distance < best.distance) {
                    best = {
                        attachment: {
                            serverMonitorId: serverMonitor.monitor_id,
                            edge: option.edge,
                        },
                        distance: option.distance,
                    };
                }
            }
        }

        return best?.attachment ?? null;
    }, [serverMonitors]);

    const findCurrentAttachment = useCallback((rect: Rect): EdgeAttachment | null => {
        return findClosestServerAttachment(rect, 2);
    }, [findClosestServerAttachment]);

    const rectsOverlapLocal = (a: Rect, b: Rect): boolean =>
        !(
            a.x + a.width <= b.x
            || b.x + b.width <= a.x
            || a.y + a.height <= b.y
            || b.y + b.height <= a.y
        );

    const movementObstacles = useCallback((skipPlacementIdx?: number): Rect[] => [
        ...serverMonitors.map(monitorAsRect),
        ...placements
            .filter((_, i) => i !== skipPlacementIdx)
            .map((p) => ({
                x: p.workspace_x,
                y: p.workspace_y,
                width: p.width,
                height: p.height,
            })),
    ], [placements, serverMonitors]);

    const moveAxisWithCollision = (
        rect: Rect,
        axis: "x" | "y",
        target: number,
        obstacles: Rect[],
    ): Rect => {
        const size = axis === "x" ? rect.width : rect.height;
        const crossStart = axis === "x" ? rect.y : rect.x;
        const crossEnd = crossStart + (axis === "x" ? rect.height : rect.width);
        const current = axis === "x" ? rect.x : rect.y;
        const delta = target - current;
        if (delta === 0) return rect;

        let resolved = target;
        for (const obstacle of obstacles) {
            const obstacleStart = axis === "x" ? obstacle.x : obstacle.y;
            const obstacleEnd = obstacleStart + (axis === "x" ? obstacle.width : obstacle.height);
            const obstacleCrossStart = axis === "x" ? obstacle.y : obstacle.x;
            const obstacleCrossEnd = obstacleCrossStart + (axis === "x" ? obstacle.height : obstacle.width);
            if (!rangesOverlap(crossStart, crossEnd, obstacleCrossStart, obstacleCrossEnd)) {
                continue;
            }

            if (delta > 0) {
                const currentEnd = current + size;
                const targetEnd = target + size;
                if (currentEnd <= obstacleStart && targetEnd > obstacleStart) {
                    resolved = Math.min(resolved, obstacleStart - size);
                }
            } else if (current >= obstacleEnd && target < obstacleEnd) {
                resolved = Math.max(resolved, obstacleEnd);
            }
        }

        const next = {...rect, [axis]: resolved};
        for (const obstacle of obstacles) {
            if (!rectsOverlapLocal(next, obstacle)) continue;
            if (delta > 0) {
                return {...next, [axis]: (axis === "x" ? obstacle.x : obstacle.y) - size};
            }
            return {
                ...next,
                [axis]: (axis === "x" ? obstacle.x + obstacle.width : obstacle.y + obstacle.height),
            };
        }
        return next;
    };

    const resolveCollision = useCallback((
        candidate: Rect,
        previous: Rect,
        obstacles: Rect[],
    ): Rect => {
        const dx = candidate.x - previous.x;
        const dy = candidate.y - previous.y;
        const xFirst = Math.abs(dx) >= Math.abs(dy);
        const axes: Array<"x" | "y"> = xFirst ? ["x", "y"] : ["y", "x"];
        let resolved = previous;
        for (const axis of axes) {
            resolved = moveAxisWithCollision(
                resolved,
                axis,
                axis === "x" ? candidate.x : candidate.y,
                obstacles,
            );
        }
        return resolved;
    }, [placements, serverMonitors]);

    const resolveInitialOverlap = useCallback((
        candidate: Rect,
        obstacles: Rect[],
    ): Rect => {
        let resolved = candidate;
        for (const obstacle of obstacles) {
            if (!rectsOverlapLocal(resolved, obstacle)) continue;
            const pushLeft = Math.abs(resolved.x + resolved.width - obstacle.x);
            const pushRight = Math.abs(obstacle.x + obstacle.width - resolved.x);
            const pushUp = Math.abs(resolved.y + resolved.height - obstacle.y);
            const pushDown = Math.abs(obstacle.y + obstacle.height - resolved.y);
            const minPush = Math.min(pushLeft, pushRight, pushUp, pushDown);

            if (minPush === pushLeft) {
                resolved = {...resolved, x: obstacle.x - resolved.width};
            } else if (minPush === pushRight) {
                resolved = {...resolved, x: obstacle.x + obstacle.width};
            } else if (minPush === pushUp) {
                resolved = {...resolved, y: obstacle.y - resolved.height};
            } else {
                resolved = {...resolved, y: obstacle.y + obstacle.height};
            }
        }
        return resolved;
    }, []);

    function onPlacementPointerDown(e: React.PointerEvent, idx: number) {
        e.preventDefault();
        e.stopPropagation();
        if (!canvasRef.current) return;
        setSelectedPlacementIdx(idx);
        const rect = canvasRef.current.getBoundingClientRect();
        const ws = canvasToWorkspace(
            e.clientX - rect.left,
            e.clientY - rect.top,
            metrics,
        );
        const p = placements[idx];
        const initialRect = {
            x: p.workspace_x,
            y: p.workspace_y,
            width: p.width,
            height: p.height,
        };
        setDrag({
            placementIdx: idx,
            grabDx: ws.x - p.workspace_x,
            grabDy: ws.y - p.workspace_y,
            originX: p.workspace_x,
            originY: p.workspace_y,
            attachment: findCurrentAttachment(initialRect),
            lastRect: initialRect,
            suppressAttachmentUntilTouch: false,
        });
        (e.target as Element).setPointerCapture?.(e.pointerId);
    }

    // Attached monitors slide freely along the server edge; detaching requires
    // a deliberate pull away from that edge.
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

        const obstacles = movementObstacles(drag.placementIdx);
        const candidate = {
            x: ws.x - drag.grabDx,
            y: ws.y - drag.grabDy,
            width: target.width,
            height: target.height,
        };
        let nextRect = candidate;
        let nextAttachment = drag.attachment;
        let suppressAttachmentUntilTouch = drag.suppressAttachmentUntilTouch;
        const detachThreshold = DETACH_THRESHOLD_PX / metrics.scale;

        if (drag.attachment) {
            const serverRect = serverRectByMonitorId(drag.attachment.serverMonitorId);
            if (!serverRect) {
                nextAttachment = null;
                suppressAttachmentUntilTouch = false;
            } else if (
                outwardDistance(candidate, serverRect, drag.attachment.edge)
                > detachThreshold
            ) {
                nextAttachment = null;
                suppressAttachmentUntilTouch = true;
            } else {
                nextRect = projectToAttachment(
                    candidate,
                    serverRect,
                    drag.attachment.edge,
                );
                if (drag.attachment.edge === "left" || drag.attachment.edge === "right") {
                    nextRect = {
                        ...nextRect,
                        y: drag.lastRect.y
                            + (nextRect.y - drag.lastRect.y) * ATTACHED_SLIDE_FACTOR,
                    };
                } else {
                    nextRect = {
                        ...nextRect,
                        x: drag.lastRect.x
                            + (nextRect.x - drag.lastRect.x) * ATTACHED_SLIDE_FACTOR,
                    };
                }
            }
        }

        if (!nextAttachment && !suppressAttachmentUntilTouch) {
            const found = findClosestServerAttachment(
                candidate,
                ATTACH_THRESHOLD_PX / metrics.scale,
            );
            if (found) {
                const serverRect = serverRectByMonitorId(found.serverMonitorId);
                if (serverRect) {
                    nextAttachment = found;
                    suppressAttachmentUntilTouch = false;
                    nextRect = projectToAttachment(candidate, serverRect, found.edge);
                }
            }
        }

        nextRect = resolveCollision(nextRect, drag.lastRect, obstacles);
        if (!nextAttachment && suppressAttachmentUntilTouch) {
            const touched = findClosestServerAttachment(
                nextRect,
                TOUCH_THRESHOLD_PX / metrics.scale,
            );
            if (touched) {
                const serverRect = serverRectByMonitorId(touched.serverMonitorId);
                if (serverRect) {
                    nextAttachment = touched;
                    suppressAttachmentUntilTouch = false;
                    nextRect = projectToAttachment(nextRect, serverRect, touched.edge);
                }
            }
        }

        if (
            nextAttachment?.serverMonitorId !== drag.attachment?.serverMonitorId
            || nextAttachment?.edge !== drag.attachment?.edge
            || suppressAttachmentUntilTouch !== drag.suppressAttachmentUntilTouch
        ) {
            setDrag((current) => {
                if (!current || current.placementIdx !== drag.placementIdx) return current;
                return {
                    ...current,
                    attachment: nextAttachment,
                    lastRect: nextRect,
                    suppressAttachmentUntilTouch,
                };
            });
        } else {
            setDrag((current) => {
                if (!current || current.placementIdx !== drag.placementIdx) return current;
                return {...current, lastRect: nextRect};
            });
        }

        const next = placements.slice();
        next[drag.placementIdx] = {
            ...target,
            workspace_x: Math.round(nextRect.x),
            workspace_y: Math.round(nextRect.y),
        };
        onChange(next);
    }, [
        drag,
        findClosestServerAttachment,
        metrics,
        movementObstacles,
        placements,
        resolveCollision,
        serverRectByMonitorId,
        onChange,
    ]);

    // Release keeps the user-guided position. Validation remains visual and blocks Save.
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
            const cursorRect = {
                x: ws.x - prev.width / 2,
                y: ws.y - prev.height / 2,
                width: prev.width,
                height: prev.height,
            };
            let chosen = cursorRect;
            const attachment = findClosestServerAttachment(
                cursorRect,
                ATTACH_THRESHOLD_PX / metrics.scale,
            );
            if (attachment) {
                const serverRect = serverRectByMonitorId(attachment.serverMonitorId);
                if (serverRect) {
                    chosen = projectToAttachment(cursorRect, serverRect, attachment.edge);
                }
            }
            chosen = resolveInitialOverlap(chosen, movementObstacles());

            const newPlacement: MonitorPlacement = {
                client_uid: prev.clientUid,
                client_monitor_id: prev.clientMonitorId,
                workspace_x: Math.round(chosen.x),
                workspace_y: Math.round(chosen.y),
                width: prev.width,
                height: prev.height,
            };
            onChange([...placements, newPlacement]);
            setSelectedPlacementIdx(placements.length);
            return null;
        });
    }, [
        findClosestServerAttachment,
        metrics,
        movementObstacles,
        placements,
        resolveInitialOverlap,
        serverRectByMonitorId,
        onChange,
    ]);

    const onPendingCancel = useCallback(() => setPendingNew(null), []);

    useEffect(() => {
        if (!pendingNew) return;
        window.addEventListener("pointermove", onPendingMove);
        window.addEventListener("pointerup", onPendingUp);
        window.addEventListener("pointercancel", onPendingCancel);
        return () => {
            window.removeEventListener("pointermove", onPendingMove);
            window.removeEventListener("pointerup", onPendingUp);
            window.removeEventListener("pointercancel", onPendingCancel);
        };
    }, [pendingNew, onPendingMove, onPendingUp, onPendingCancel]);

    function removePlacement(idx: number) {
        const next = placements.slice();
        next.splice(idx, 1);
        onChange(next);
        setSelectedPlacementIdx((selected) => {
            if (selected === null) return null;
            if (selected === idx) return null;
            return selected > idx ? selected - 1 : selected;
        });
    }

    function movePlacementByKeyboard(
        e: React.KeyboardEvent<HTMLDivElement>,
        idx: number,
    ) {
        const step = e.shiftKey ? 10 : 1;
        const deltaByKey: Record<string, {dx: number; dy: number}> = {
            ArrowLeft: {dx: -step, dy: 0},
            ArrowRight: {dx: step, dy: 0},
            ArrowUp: {dx: 0, dy: -step},
            ArrowDown: {dx: 0, dy: step},
        };

        if (e.key === "Delete" || e.key === "Backspace") {
            e.preventDefault();
            removePlacement(idx);
            return;
        }

        const delta = deltaByKey[e.key];
        if (!delta) return;
        e.preventDefault();
        setSelectedPlacementIdx(idx);
        const target = placements[idx];
        if (!target) return;
        const previousRect = {
            x: target.workspace_x,
            y: target.workspace_y,
            width: target.width,
            height: target.height,
        };
        const resolved = resolveCollision(
            {
                ...previousRect,
                x: previousRect.x + delta.dx,
                y: previousRect.y + delta.dy,
            },
            previousRect,
            movementObstacles(idx),
        );
        const next = placements.slice();
        next[idx] = {
            ...target,
            workspace_x: Math.round(resolved.x),
            workspace_y: Math.round(resolved.y),
        };
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
        const isOverlap = validation.overlappingIndices.has(idx);
        const isOrphan = validation.notAdjacentToServerIndices.has(idx);
        const isBad = isOverlap || isOrphan;
        const isDragging = drag?.placementIdx === idx;
        const badTitle = isOverlap
            ? `overlaps with another monitor - drag to a free area`
            : `monitor detached - drag against a server edge`;
        return (
            <div
                key={`p-${p.client_uid}-${p.client_monitor_id}-${idx}`}
                className="focus:outline-none"
                onPointerDown={(e) => onPlacementPointerDown(e, idx)}
                onFocus={() => setSelectedPlacementIdx(idx)}
                onKeyDown={(e) => movePlacementByKeyboard(e, idx)}
                tabIndex={0}
                role="button"
                aria-label={`${client?.name || p.client_uid} monitor ${p.client_monitor_id}`}
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
                    outline: "none",
                    boxShadow: isDragging
                        ? "0 4px 12px rgba(0,0,0,0.25)"
                        : "none",
                    zIndex: isDragging ? 10 : 1,
                    touchAction: "none",
                }}
                title={
                    isBad
                        ? `${client?.name || p.client_uid} · monitor #${p.client_monitor_id} · ${badTitle}`
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
                        <AlertTriangle size={11} style={{margin: "2.5px auto"}} />
                    </div>
                )}
                <button
                    type="button"
                    aria-label={`Remove ${client?.name || p.client_uid} monitor ${p.client_monitor_id}`}
                    onPointerDown={(e) => {
                        e.preventDefault();
                        e.stopPropagation();
                    }}
                    onClick={(e) => {
                        e.stopPropagation();
                        removePlacement(idx);
                    }}
                    title="Remove from workspace"
                    className="transition-all duration-150 hover:scale-105 active:scale-95 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2"
                    style={{
                        position: "absolute",
                        top: 4,
                        right: 4,
                        width: 22,
                        height: 22,
                        borderRadius: "50%",
                        border: "1px solid rgba(255,255,255,0.45)",
                        backgroundColor: "rgba(0,0,0,0.62)",
                        color: "white",
                        padding: 0,
                        cursor: "pointer",
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        outlineColor: "var(--app-primary-light)",
                    }}
                >
                    <X size={13} strokeWidth={2.5} />
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
                        <AlertTriangle size={13} />
                        <span>{validationSummary}</span>
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
