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
 *
 */

import type {
    Edge,
    LayoutBinding,
    LayoutSlot,
    MonitorInfo,
    MonitorPlacement,
} from "../api/Interface";

// Mirrors utils.screen._monitor's overlap/routing helpers - keep in lock-step with the Python side.

export function slotsOverlap(a: LayoutSlot, b: LayoutSlot): boolean {
    if (a.monitor_id !== b.monitor_id || a.edge !== b.edge) return false;
    // Touching at a single point counts as disjoint (clean split at 0.5 is valid).
    return !(a.segment_end <= b.segment_start || b.segment_end <= a.segment_start);
}

export interface LayoutValidationResult {
    ok: boolean;
    errors: string[];
    errorsByIndex: Record<number, string[]>;
}

export function validateLayout(
    bindings: LayoutBinding[],
    knownMonitorIds?: Set<number>,
): LayoutValidationResult {
    const errors: string[] = [];
    const errorsByIndex: Record<number, string[]> = {};

    const pushError = (i: number, msg: string) => {
        errors.push(msg);
        if (!errorsByIndex[i]) errorsByIndex[i] = [];
        errorsByIndex[i].push(msg);
    };

    if (knownMonitorIds) {
        bindings.forEach((b, i) => {
            if (!knownMonitorIds.has(b.slot.monitor_id)) {
                pushError(
                    i,
                    `Unknown monitor #${b.slot.monitor_id} for client ${b.client_uid}`,
                );
            }
        });
    }

    for (let i = 0; i < bindings.length; i++) {
        for (let j = i + 1; j < bindings.length; j++) {
            if (slotsOverlap(bindings[i].slot, bindings[j].slot)) {
                const msg =
                    `Overlap: client ${bindings[i].client_uid} and ` +
                    `${bindings[j].client_uid} on monitor #${bindings[i].slot.monitor_id} ` +
                    `${bindings[i].slot.edge}`;
                pushError(i, msg);
                pushError(j, msg);
            }
        }
    }

    return {ok: errors.length === 0, errors, errorsByIndex};
}

export interface ViewMetrics {
    scale: number;
    offsetX: number;
    offsetY: number;
}

// Workspace-bounds-driven viewport. Used by the LayoutEditor where the visible
// area must include both server monitors and unsubmitted placements.
export function computeViewMetrics(
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

export function workspaceToCanvas(
    x: number,
    y: number,
    m: ViewMetrics,
): {x: number; y: number} {
    return {x: x * m.scale + m.offsetX, y: y * m.scale + m.offsetY};
}

export function canvasToWorkspace(
    x: number,
    y: number,
    m: ViewMetrics,
): {x: number; y: number} {
    return {x: (x - m.offsetX) / m.scale, y: (y - m.offsetY) / m.scale};
}

export interface CanvasMetrics {
    offsetX: number;
    offsetY: number;
    scale: number;
    width: number;
    height: number;
}

export function computeCanvasMetrics(
    monitors: MonitorInfo[],
    canvasWidth: number,
    canvasHeight: number,
    padding = 24,
): CanvasMetrics {
    if (monitors.length === 0) {
        return {offsetX: 0, offsetY: 0, scale: 1, width: 0, height: 0};
    }
    const minX = Math.min(...monitors.map((m) => m.min_x));
    const minY = Math.min(...monitors.map((m) => m.min_y));
    const maxX = Math.max(...monitors.map((m) => m.max_x));
    const maxY = Math.max(...monitors.map((m) => m.max_y));
    const w = Math.max(1, maxX - minX);
    const h = Math.max(1, maxY - minY);
    const usableW = Math.max(1, canvasWidth - padding * 2);
    const usableH = Math.max(1, canvasHeight - padding * 2);
    const scale = Math.min(usableW / w, usableH / h);
    return {
        offsetX: padding - minX * scale,
        offsetY: padding - minY * scale,
        scale,
        width: w * scale,
        height: h * scale,
    };
}

export function monitorRectInCanvas(
    monitor: MonitorInfo,
    metrics: CanvasMetrics,
) {
    return {
        left: monitor.min_x * metrics.scale + metrics.offsetX,
        top: monitor.min_y * metrics.scale + metrics.offsetY,
        width: (monitor.max_x - monitor.min_x) * metrics.scale,
        height: (monitor.max_y - monitor.min_y) * metrics.scale,
    };
}

// Picks the monitor edge closest to a canvas point (canvas px) for drop targeting.
export interface EdgeHit {
    monitorId: number;
    edge: Edge;
    axisNorm: number;
    distancePx: number;
}

export function pickClosestEdge(
    canvasX: number,
    canvasY: number,
    monitors: MonitorInfo[],
    metrics: CanvasMetrics,
): EdgeHit | null {
    let best: EdgeHit | null = null;
    for (const m of monitors) {
        const r = monitorRectInCanvas(m, metrics);
        const candidates: Array<{edge: Edge; dist: number; axisNorm: number}> = [
            {
                edge: "left",
                dist: Math.abs(canvasX - r.left),
                axisNorm: (canvasY - r.top) / Math.max(1, r.height),
            },
            {
                edge: "right",
                dist: Math.abs(canvasX - (r.left + r.width)),
                axisNorm: (canvasY - r.top) / Math.max(1, r.height),
            },
            {
                edge: "top",
                dist: Math.abs(canvasY - r.top),
                axisNorm: (canvasX - r.left) / Math.max(1, r.width),
            },
            {
                edge: "bottom",
                dist: Math.abs(canvasY - (r.top + r.height)),
                axisNorm: (canvasX - r.left) / Math.max(1, r.width),
            },
        ];
        for (const c of candidates) {
            if (c.axisNorm < 0 || c.axisNorm > 1) continue;
            if (best === null || c.dist < best.distancePx) {
                best = {
                    monitorId: m.monitor_id,
                    edge: c.edge,
                    axisNorm: Math.max(0, Math.min(1, c.axisNorm)),
                    distancePx: c.dist,
                };
            }
        }
    }
    return best;
}

// Free segment disjoint from existing slots on the same (monitor, edge); null if fully occupied.
export function findFreeSegment(
    bindings: LayoutBinding[],
    monitorId: number,
    edge: Edge,
    preferredCenter: number,
    minWidth = 0.1,
): [number, number] | null {
    const taken = bindings
        .filter(
            (b) => b.slot.monitor_id === monitorId && b.slot.edge === edge,
        )
        .map((b) => [b.slot.segment_start, b.slot.segment_end] as [number, number])
        .sort((a, b) => a[0] - b[0]);

    const gaps: Array<[number, number]> = [];
    let cursor = 0;
    for (const [s, e] of taken) {
        if (s > cursor) gaps.push([cursor, s]);
        cursor = Math.max(cursor, e);
    }
    if (cursor < 1) gaps.push([cursor, 1]);

    let best: [number, number] | null = null;
    let bestDist = Infinity;
    for (const [s, e] of gaps) {
        if (e - s < minWidth) continue;
        const mid = (s + e) / 2;
        const dist = Math.abs(mid - preferredCenter);
        if (dist < bestDist) {
            best = [s, e];
            bestDist = dist;
        }
    }
    return best;
}


// Workspace placement helpers. Adjacency in a shared virtual workspace replaces the old TOP/BOTTOM/LEFT/RIGHT enum.

export interface Rect {
    x: number;
    y: number;
    width: number;
    height: number;
}

export function monitorAsRect(m: MonitorInfo): Rect {
    return {
        x: m.min_x,
        y: m.min_y,
        width: m.max_x - m.min_x,
        height: m.max_y - m.min_y,
    };
}

export function placementAsRect(p: MonitorPlacement): Rect {
    return {x: p.workspace_x, y: p.workspace_y, width: p.width, height: p.height};
}

export function rectsOverlap(a: Rect, b: Rect): boolean {
    return !(
        a.x + a.width <= b.x
        || b.x + b.width <= a.x
        || a.y + a.height <= b.y
        || b.y + b.height <= a.y
    );
}

/** True if `a` shares an edge with `b` within `tolerance` and orthogonal ranges overlap. */
export function rectsAdjacent(a: Rect, b: Rect, tolerance = 2): boolean {
    const aRight = a.x + a.width;
    const aBottom = a.y + a.height;
    const bRight = b.x + b.width;
    const bBottom = b.y + b.height;

    const yOverlap = a.y < bBottom && b.y < aBottom;
    const xOverlap = a.x < bRight && b.x < aRight;

    const horizontalAbut =
        yOverlap
        && (Math.abs(aRight - b.x) <= tolerance || Math.abs(bRight - a.x) <= tolerance);
    const verticalAbut =
        xOverlap
        && (Math.abs(aBottom - b.y) <= tolerance || Math.abs(bBottom - a.y) <= tolerance);

    return horizontalAbut || verticalAbut;
}

export function isAdjacentToAny(
    candidate: Rect,
    others: Rect[],
    tolerance = 2,
): boolean {
    for (const r of others) {
        if (rectsAdjacent(candidate, r, tolerance)) return true;
    }
    return false;
}

export function workspaceBounds(
    serverMonitors: MonitorInfo[],
    placements: MonitorPlacement[],
): Rect {
    if (serverMonitors.length === 0 && placements.length === 0) {
        return {x: 0, y: 0, width: 1, height: 1};
    }
    const rects: Rect[] = [
        ...serverMonitors.map(monitorAsRect),
        ...placements.map(placementAsRect),
    ];
    const minX = Math.min(...rects.map(r => r.x));
    const minY = Math.min(...rects.map(r => r.y));
    const maxX = Math.max(...rects.map(r => r.x + r.width));
    const maxY = Math.max(...rects.map(r => r.y + r.height));
    return {x: minX, y: minY, width: maxX - minX, height: maxY - minY};
}

// Snap top-left to nearby monitor edges within `thresholdPx` (workspace px); prefers flush alignment.
export function snapRect(
    candidate: Rect,
    others: Rect[],
    thresholdPx: number,
): {x: number; y: number} {
    let snapX = candidate.x;
    let snapY = candidate.y;
    let bestDX = thresholdPx;
    let bestDY = thresholdPx;

    for (const r of others) {
        const candidates = [
            {target: r.x + r.width, current: candidate.x},
            {target: r.x - candidate.width, current: candidate.x},
            {target: r.x, current: candidate.x},
            {target: r.x + r.width - candidate.width, current: candidate.x},
        ];
        for (const c of candidates) {
            const d = Math.abs(c.target - c.current);
            if (d < bestDX) {
                bestDX = d;
                snapX = c.target;
            }
        }

        const vCandidates = [
            {target: r.y + r.height, current: candidate.y},
            {target: r.y - candidate.height, current: candidate.y},
            {target: r.y, current: candidate.y},
            {target: r.y + r.height - candidate.height, current: candidate.y},
        ];
        for (const c of vCandidates) {
            const d = Math.abs(c.target - c.current);
            if (d < bestDY) {
                bestDY = d;
                snapY = c.target;
            }
        }
    }

    return {x: snapX, y: snapY};
}

export interface PlacementValidationResult {
    ok: boolean;
    overlappingIndices: Set<number>;
    notAdjacentToServerIndices: Set<number>;
    errors: string[];
}

// Reject placements overlapping a server monitor, overlapping each other,
// or not abutting any server monitor (chained client-only hops break the
// reverse-routing path back to the server).
export function validatePlacements(
    serverMonitors: MonitorInfo[],
    placements: MonitorPlacement[],
): PlacementValidationResult {
    const overlapping = new Set<number>();
    const notAdjacent = new Set<number>();
    const errors: string[] = [];
    const serverRects = serverMonitors.map(monitorAsRect);

    placements.forEach((p, i) => {
        const r = placementAsRect(p);
        for (const sr of serverRects) {
            if (rectsOverlap(r, sr)) {
                overlapping.add(i);
                errors.push(
                    `Client ${p.client_uid} monitor ${p.client_monitor_id} `
                    + `overlaps a server monitor`,
                );
                break;
            }
        }
        // Server-adjacency rule. Skipped while serverRects is empty so
        // legacy clients without server monitor info don't fail outright.
        if (serverRects.length > 0 && !isAdjacentToAny(r, serverRects)) {
            notAdjacent.add(i);
            errors.push(
                `Client ${p.client_uid} monitor ${p.client_monitor_id} `
                + `is not adjacent to any server monitor`,
            );
        }
    });

    for (let i = 0; i < placements.length; i++) {
        for (let j = i + 1; j < placements.length; j++) {
            if (rectsOverlap(placementAsRect(placements[i]), placementAsRect(placements[j]))) {
                overlapping.add(i);
                overlapping.add(j);
                errors.push(
                    `Client ${placements[i].client_uid}/${placements[i].client_monitor_id} `
                    + `overlaps ${placements[j].client_uid}/${placements[j].client_monitor_id}`,
                );
            }
        }
    }

    return {
        ok: errors.length === 0,
        overlappingIndices: overlapping,
        notAdjacentToServerIndices: notAdjacent,
        errors,
    };
}

// Default placement for a fresh client monitor: right edge of the workspace at the primary monitor's Y.
export function suggestInitialPlacement(
    serverMonitors: MonitorInfo[],
    placements: MonitorPlacement[],
): {x: number; y: number} {
    if (serverMonitors.length === 0 && placements.length === 0) {
        return {x: 0, y: 0};
    }
    const bounds = workspaceBounds(serverMonitors, placements);
    const primary = serverMonitors.find(m => m.is_primary) || serverMonitors[0];
    const y = primary ? primary.min_y : bounds.y;
    return {x: bounds.x + bounds.width + 20, y};
}
