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

import * as React from "react";
import {createPortal} from "react-dom";
import {classNames} from '../../commons/utils';

const OVERLAY_SCROLLBAR_HIDE_DELAY_MS = 220;

export interface ScrollAreaProps extends React.HTMLAttributes<HTMLDivElement> {
    extraPadding?: string;
    children: React.ReactNode;
}

const ScrollArea = React.forwardRef<HTMLDivElement, ScrollAreaProps>(
    ({className, children, extraPadding: _extraPadding, ...props}, ref) => {
        const scrollRef = React.useRef<HTMLDivElement | null>(null);
        const scrollIdleTimeoutRef = React.useRef<ReturnType<typeof setTimeout> | null>(null);
        const [metrics, setMetrics] = React.useState({
            scrollTop: 0,
            clientHeight: 0,
            scrollHeight: 0,
            viewportTop: 0,
            viewportRight: 0,
        });
        const [hovered, setHovered] = React.useState(false);
        const [scrolling, setScrolling] = React.useState(false);
        const [dragging, setDragging] = React.useState(false);
        const thumbDragRef = React.useRef<{
            startY: number;
            startScrollTop: number;
        } | null>(null);
        const useOverlayScrollbar = true;

        const setRefs = React.useCallback((node: HTMLDivElement | null) => {
            scrollRef.current = node;
            if (typeof ref === 'function') {
                ref(node);
            } else if (ref) {
                ref.current = node;
            }
        }, [ref]);

        const updateMetrics = React.useCallback(() => {
            const node = scrollRef.current;
            if (!node) return;
            const rect = node.getBoundingClientRect();
            setMetrics({
                scrollTop: node.scrollTop,
                clientHeight: node.clientHeight,
                scrollHeight: node.scrollHeight,
                viewportTop: rect.top,
                viewportRight: rect.right,
            });
        }, []);

        React.useLayoutEffect(() => {
            if (!useOverlayScrollbar) return;
            const node = scrollRef.current;
            if (!node) return;

            updateMetrics();
            const resizeObserver = new ResizeObserver(updateMetrics);
            resizeObserver.observe(node);
            for (const child of Array.from(node.children)) {
                resizeObserver.observe(child);
            }
            window.addEventListener("resize", updateMetrics);
            window.addEventListener("scroll", updateMetrics, true);

            return () => {
                resizeObserver.disconnect();
                window.removeEventListener("resize", updateMetrics);
                window.removeEventListener("scroll", updateMetrics, true);
            };
        }, [children, updateMetrics, useOverlayScrollbar]);

        React.useEffect(() => {
            return () => {
                if (scrollIdleTimeoutRef.current) {
                    clearTimeout(scrollIdleTimeoutRef.current);
                }
            };
        }, []);

        const onScroll = (event: React.UIEvent<HTMLDivElement>) => {
            props.onScroll?.(event);
            if (!useOverlayScrollbar) return;
            updateMetrics();
            setScrolling(true);
            if (scrollIdleTimeoutRef.current) {
                clearTimeout(scrollIdleTimeoutRef.current);
            }
            scrollIdleTimeoutRef.current = setTimeout(() => {
                setScrolling(false);
            }, OVERLAY_SCROLLBAR_HIDE_DELAY_MS);
        };

        const showScrollActivity = React.useCallback(() => {
            setScrolling(true);
            if (scrollIdleTimeoutRef.current) {
                clearTimeout(scrollIdleTimeoutRef.current);
            }
            scrollIdleTimeoutRef.current = setTimeout(() => {
                setScrolling(false);
            }, OVERLAY_SCROLLBAR_HIDE_DELAY_MS);
        }, []);

        const onMouseEnter = (event: React.MouseEvent<HTMLDivElement>) => {
            props.onMouseEnter?.(event);
            if (useOverlayScrollbar) setHovered(true);
        };

        const onMouseLeave = (event: React.MouseEvent<HTMLDivElement>) => {
            props.onMouseLeave?.(event);
            if (useOverlayScrollbar) setHovered(false);
        };

        const canScroll = metrics.scrollHeight > metrics.clientHeight + 1;
        const trackInset = 4;
        const trackHeight = Math.max(0, metrics.clientHeight - trackInset * 2);
        const thumbHeight = canScroll
            ? Math.max(24, (metrics.clientHeight / metrics.scrollHeight) * trackHeight)
            : 0;
        const maxScrollTop = Math.max(1, metrics.scrollHeight - metrics.clientHeight);
        const maxThumbTop = Math.max(0, trackHeight - thumbHeight);
        const thumbTop = trackInset + (metrics.scrollTop / maxScrollTop) * maxThumbTop;
        const showThumb = useOverlayScrollbar && canScroll && (hovered || scrolling || dragging);

        React.useEffect(() => {
            if (!dragging) return;
            const onMouseMove = (event: MouseEvent) => {
                const node = scrollRef.current;
                const dragStart = thumbDragRef.current;
                if (!node || !dragStart) return;
                const deltaY = event.clientY - dragStart.startY;
                const scrollDelta = (deltaY / Math.max(1, maxThumbTop)) * maxScrollTop;
                node.scrollTop = dragStart.startScrollTop + scrollDelta;
                updateMetrics();
            };
            const onMouseUp = () => {
                thumbDragRef.current = null;
                setDragging(false);
                setScrolling(false);
            };
            window.addEventListener("mousemove", onMouseMove);
            window.addEventListener("mouseup", onMouseUp);
            return () => {
                window.removeEventListener("mousemove", onMouseMove);
                window.removeEventListener("mouseup", onMouseUp);
            };
        }, [dragging, maxScrollTop, maxThumbTop, updateMetrics]);

        const scrollToPointer = (clientY: number) => {
            const node = scrollRef.current;
            if (!node) return;
            const trackTop = metrics.viewportTop + trackInset;
            const y = clientY - trackTop - thumbHeight / 2;
            node.scrollTop = (y / Math.max(1, maxThumbTop)) * maxScrollTop;
            updateMetrics();
            setScrolling(true);
        };

        const onOverlayMouseDown = (event: React.MouseEvent<HTMLDivElement>) => {
            event.preventDefault();
            event.stopPropagation();
            const node = scrollRef.current;
            if (!node) return;
            if ((event.target as HTMLElement).dataset.scrollbarThumb === "true") {
                thumbDragRef.current = {
                    startY: event.clientY,
                    startScrollTop: node.scrollTop,
                };
                setDragging(true);
                setScrolling(true);
                return;
            }
            scrollToPointer(event.clientY);
        };

        const onOverlayWheel = (event: React.WheelEvent<HTMLDivElement>) => {
            event.preventDefault();
            event.stopPropagation();
            const node = scrollRef.current;
            if (!node) return;
            node.scrollTop += event.deltaY;
            updateMetrics();
            showScrollActivity();
        };

        const overlay = useOverlayScrollbar && canScroll
            ? createPortal(
                <div
                    aria-hidden="true"
                    className="custom-scrollbar-overlay"
                    onMouseDown={onOverlayMouseDown}
                    onWheel={onOverlayWheel}
                    onMouseEnter={() => setHovered(true)}
                    onMouseLeave={() => {
                        if (!dragging) setHovered(false);
                    }}
                    style={{
                        opacity: showThumb ? 1 : 0,
                        top: metrics.viewportTop + trackInset,
                        left: metrics.viewportRight - 8,
                        height: trackHeight,
                    }}
                >
                    <div
                        data-scrollbar-thumb="true"
                        className="custom-scrollbar-overlay-thumb"
                        style={{
                            height: thumbHeight,
                            transform: `translateY(${thumbTop - trackInset}px)`,
                        }}
                    />
                </div>,
                document.body,
            )
            : null;

        return (
            <div
                {...props}
                ref={setRefs}
                data-scrollbar-gutter="overlay"
                data-scrollbar-mode="overlay"
                className={classNames(
                    "custom-scrollbar",
                    className
                )}
                onMouseEnter={onMouseEnter}
                onMouseLeave={onMouseLeave}
                onScroll={onScroll}
            >
                {overlay}
                {children}
            </div>
        );
    }
);

ScrollArea.displayName = "ScrollArea";

export {ScrollArea};
