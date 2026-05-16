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

// Mirror of utils.screen._monitor's overlap / routing helpers so the GUI
// can validate the layout the user is building without a round-trip to
// the daemon. Kept in lock-step with the Python implementation.

export function slotsOverlap(a: LayoutSlot, b: LayoutSlot): boolean {
    if (a.monitor_id !== b.monitor_id || a.edge !== b.edge) return false;
    // Touching at a single point counts as disjoint so a clean split at
    // 0.5 between two slots is valid.
    return !(a.segment_end <= b.segment_start || b.segment_end <= a.segment_start);
}

export interface LayoutValidationResult {
    ok: boolean;
    errors: string[];
    // Maps every offending binding index to the human-readable error so
    // the editor can highlight rows in place.
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

// Geometric helpers used by the visual editor.

export interface CanvasMetrics {
    // Pixel offset to apply to every monitor's coords so the smallest
    // (min_x, min_y) sits at (0, 0) inside the canvas.
    offsetX: number;
    offsetY: number;
    // Scale factor mapping OS pixels to canvas pixels.
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

// Picks the closest edge of any monitor to a canvas point (in canvas px).
// Used during drag to figure out where a client chip is being dropped.
export interface EdgeHit {
    monitorId: number;
    edge: Edge;
    // 0..1 normalized position along the edge's secondary axis.
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
            // Skip if the secondary axis is outside the monitor (we only
            // want hits along the actual physical edge length).
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

// Finds a free segment to drop a new slot into. Returns a [start, end]
// pair guaranteed to be disjoint from every existing slot on the same
// (monitor, edge). Returns null if the edge is fully occupied.
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

    // Build the list of free gaps along [0, 1].
    const gaps: Array<[number, number]> = [];
    let cursor = 0;
    for (const [s, e] of taken) {
        if (s > cursor) gaps.push([cursor, s]);
        cursor = Math.max(cursor, e);
    }
    if (cursor < 1) gaps.push([cursor, 1]);

    // Pick the gap whose middle is closest to the preferred center.
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


// ============================================================================
// Workspace placement helpers (new model).
// Each monitor — server or client — is a rectangle in a shared virtual
// workspace. Adjacency replaces the old LEFT/RIGHT/TOP/BOTTOM enum: when
// a cursor reaches the edge of one rect AND another rect abuts it at
// that secondary coordinate, the cursor crosses.
// ============================================================================

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

/** ``true`` if ``a`` shares an edge with ``b`` (within ``tolerance`` px on
 * the abutting axis) and their orthogonal ranges overlap. Used by the
 * layout editor to prevent client monitors from drifting into empty
 * space — every placement must touch at least one server monitor or
 * another already-placed client. */
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

/** ``true`` if ``candidate`` is adjacent (in the {@link rectsAdjacent}
 * sense) to at least one rect in ``others``. */
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

// Computes the combined bbox of every monitor (server + client placed
// in the workspace) so the canvas can scale to fit them all.
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

// Snap a candidate rect to nearby monitor edges within `thresholdPx`
// (interpreted in workspace pixels). Returns the snapped (x, y) for the
// rect's top-left corner. Prefers edge-flush alignment so the user can
// place a client monitor exactly touching the server's right edge.
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
        // Horizontal snaps: candidate.left ↔ r.right, candidate.right ↔ r.left,
        // and left/right alignment (same-X) for stacked layouts.
        const candidates = [
            {target: r.x + r.width, current: candidate.x},                       // right-of
            {target: r.x - candidate.width, current: candidate.x},               // left-of
            {target: r.x, current: candidate.x},                                 // align-left
            {target: r.x + r.width - candidate.width, current: candidate.x},     // align-right
        ];
        for (const c of candidates) {
            const d = Math.abs(c.target - c.current);
            if (d < bestDX) {
                bestDX = d;
                snapX = c.target;
            }
        }

        // Vertical snaps mirror the above.
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
    errors: string[];
}

// Reject any placement that overlaps a server monitor OR another
// placement. Server monitors stay fixed; clients orbit around them.
export function validatePlacements(
    serverMonitors: MonitorInfo[],
    placements: MonitorPlacement[],
): PlacementValidationResult {
    const overlapping = new Set<number>();
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

    return {ok: errors.length === 0, overlappingIndices: overlapping, errors};
}

// Default placement for a fresh client monitor: tack it onto the right
// edge of the workspace at the primary server monitor's Y. Keeps the
// initial UI un-cluttered; the user can drag from there.
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
