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

/**
 * React component for displaying daemon logs in the GUI
 *
 * Reads log files directly from the filesystem using Tauri commands.
 */

import React, {useEffect, useMemo, useRef, useState} from 'react';
import {invoke} from '@tauri-apps/api/core';
import {platform} from '@tauri-apps/plugin-os';
import * as DropdownMenu from '@radix-ui/react-dropdown-menu';
import {ScrollArea} from './scrollbar';
import {Tooltip} from './tooltip';
import {Check, ChevronDown, Copy, ExternalLink, FileText, Pause, Play, RefreshCw, Search, WrapText, X} from 'lucide-react';
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from './select';

interface LogViewerProps {
    active?: boolean;
    // Optional: customize the number of lines to fetch
    initialLines?: number;
    // Optional: auto-refresh interval in milliseconds
    refreshInterval?: number;
}

interface LogResponse {
    logs: string[];
    total_lines: number;
    log_file: string;
}

export const DaemonLogViewer: React.FC<LogViewerProps> = ({
                                                              active = true,
                                                              initialLines = 100,
                                                              refreshInterval = 5000, // 5 seconds
                                                          }) => {
    const [logs, setLogs] = useState<string[]>([]);
    const [loading, setLoading] = useState<boolean>(false);
    const [error, setError] = useState<string | null>(null);
    const [autoRefresh, setAutoRefresh] = useState<boolean>(true);
    const [numLines, setNumLines] = useState<number>(initialLines);
    const [wrapLines, setWrapLines] = useState<boolean>(true);
    const [searchQuery, setSearchQuery] = useState<string>('');
    const [actionBusy, setActionBusy] = useState(false);
    const [actionMessage, setActionMessage] = useState('');
    const [actionError, setActionError] = useState(false);
    const actionPending = useRef(false);
    const mounted = useRef(true);
    useEffect(() => {
        mounted.current = true;
        return () => { mounted.current = false; };
    }, []);
    const logEndRef = useRef<HTMLDivElement>(null);
    const scrollContainerRef = useRef<HTMLDivElement>(null);
    const isAtBottomRef = useRef<boolean>(true);

    // Function to fetch logs from daemon log file
    const fetchLogs = async (lines: number = numLines) => {
        setLoading(true);
        setError(null);

        try {
            const response = await invoke<LogResponse>('read_daemon_logs', {
                numLines: lines,
                all: false,
            });

            setLogs(response.logs);
        } catch (err) {
            setError(`${err}`);
        } finally {
            setLoading(false);
        }
    };

    // Auto-scroll to bottom when new logs arrive
    const scrollToBottom = () => {
        if (isAtBottomRef.current) {
            logEndRef.current?.scrollIntoView({behavior: 'smooth'});
        }
    };

    const handleScroll = () => {
        const el = scrollContainerRef.current;
        if (!el) return;
        const threshold = 40; // px from bottom to consider "at bottom"
        isAtBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight <= threshold;
    };

    // Initial load
    useEffect(() => {
        if (active) fetchLogs();
    }, [active]);

    // Auto-refresh
    useEffect(() => {
        if (!active || !autoRefresh) return;

        const interval = setInterval(() => {
            fetchLogs();
        }, refreshInterval);

        return () => clearInterval(interval);
    }, [active, autoRefresh, refreshInterval, numLines]);

    // Scroll to bottom when logs update
    useEffect(() => {
        scrollToBottom();
    }, [logs]);

    // Parse log line to extract level and color
    const parseLogLine = (line: string) => {
        // Logger should start with a letter and may contain word chars, parens and dots
        // e.g. "Daemon", "ConnectionHandler", "MessageExchange(Handshake_192.168.1.77)".
        // Using [A-Za-z][\w().]* prevents false matches.
        const match = line.match(/^\[([^\]]+)\]\s+\[([^\]]+)\]\s+(.*)\s*\[([A-Za-z][\w().]*)\](.*)$/);
        if (match) {
            const [, timestamp, level, message, logger, extra] = match;
            return {timestamp, level: level.trim().toUpperCase(), logger, message: message.trimEnd(), extra: extra.trim()};
        }
        return {timestamp: '', level: 'INFO', logger: '', message: line, extra: ''};
    };

    const getLevelColor = (level: string): string => {
        switch (level) {
            case 'DEBUG':
                return 'text-gray-400';
            case 'INFO':
                return 'text-blue-400';
            case 'WARNING':
                return 'text-yellow-400';
            case 'ERROR':
                return 'text-red-400';
            case 'CRITICAL':
                return 'text-red-500 font-bold';
            default:
                return 'text-gray-500';
        }
    };

    // Fuzzy search function
    const fuzzyMatch = (text: string, query: string): boolean => {
        if (!query) return true;
        const textLower = text.toLowerCase();
        const queryLower = query.toLowerCase();

        // Direct substring match (fast path)
        if (textLower.includes(queryLower)) return true;

        // Fuzzy match: check if all query chars appear in order
        let queryIndex = 0;
        for (let i = 0; i < textLower.length && queryIndex < queryLower.length; i++) {
            if (textLower[i] === queryLower[queryIndex]) {
                queryIndex++;
            }
        }
        return queryIndex === queryLower.length;
    };

    // Highlight matching text
    const highlightMatch = (text: string, query: string): React.ReactNode => {
        if (!query) return text;

        const lowerText = text.toLowerCase();
        const lowerQuery = query.toLowerCase();
        const index = lowerText.indexOf(lowerQuery);

        if (index === -1) return text;

        return (
            <>
                {text.slice(0, index)}
                <span className="bg-yellow-500/30 text-yellow-200 font-semibold">
          {text.slice(index, index + query.length)}
        </span>
                {text.slice(index + query.length)}
            </>
        );
    };

    // Filter logs based on search query
    const filteredLogs = useMemo(() => {
        if (!searchQuery.trim()) return logs;
        return logs.filter(line => fuzzyMatch(line, searchQuery));
    }, [logs, searchQuery]);

    const runLogAction = async (action: 'visible' | 'all' | 'open') => {
        if (actionPending.current) return;
        actionPending.current = true;
        setActionBusy(true);
        setActionMessage('');
        setActionError(false);
        // Capture the displayed snapshot before any async work or auto-refresh.
        const visibleText = filteredLogs.join('\n');
        try {
            if (action === 'open') {
                await invoke('open_daemon_log');
            } else {
                const text = action === 'visible' ? visibleText :
                    (await invoke<LogResponse>('read_daemon_logs', {numLines: 0, all: true})).logs.join('\n');
                if (platform() === 'macos') {
                    // WKWebView can lose clipboard user activation while reading
                    // the full file or closing the menu. Use the native pasteboard.
                    await invoke('copy_log_text', {text});
                } else {
                    await navigator.clipboard.writeText(text);
                }
            }
            if (mounted.current) setActionMessage(action === 'open' ? 'Log file opened' : 'Copied to clipboard');
        } catch (err) {
            if (mounted.current) {
                setActionError(true);
                setActionMessage(`${action === 'open' ? 'Could not open log file' : 'Could not copy logs'}: ${err}`);
            }
        } finally {
            actionPending.current = false;
            if (mounted.current) setActionBusy(false);
        }
    };
    const actionStyle = {backgroundColor: 'var(--app-input-bg)', color: 'var(--app-text-muted)'};
    const actionClass = 'inline-flex items-center gap-1.5 px-2 py-1.5 rounded-md text-xs font-medium cursor-pointer hover:opacity-80 disabled:opacity-50 disabled:cursor-not-allowed';

    return (
        <div className="daemon-log-viewer flex flex-col h-full min-h-0">
            {/* Header */}
            <div className="border-b" style={{
                borderColor: 'var(--app-input-border)',
                backgroundColor: 'var(--app-card-bg)',
            }}>
                {/* First row - Controls */}
                <div className="flex gap-2 items-center justify-between px-3 py-2.5">
                    <div className="flex items-center gap-2">
                        {/* Number of lines selector */}
                        <div className="flex items-center gap-1.5">
                            <label className="text-xs font-medium"
                                   style={{color: 'var(--app-text-muted)'}}>Lines</label>
                            <Select
                                value={String(numLines)}
                                onValueChange={(nextValue) => {
                                    const value = parseInt(nextValue, 10);
                                    setNumLines(value);
                                    fetchLogs(value);
                                }}
                            >
                                <SelectTrigger
                                    className="h-auto w-[68px] border rounded-md px-1.5 py-0.5 text-xs font-medium transition-all cursor-pointer hover:border-opacity-70 focus:outline-none focus:ring-2 focus:ring-opacity-50 shadow-none"
                                    style={{
                                        backgroundColor: 'var(--app-input-bg)',
                                        borderColor: 'var(--app-border)',
                                        color: 'var(--app-text-primary)',
                                    }}
                                >
                                    <SelectValue/>
                                </SelectTrigger>
                                <SelectContent
                                    position="item-aligned"
                                    className="min-w-[68px] text-xs"
                                    style={{
                                        backgroundColor: 'var(--app-bg-secondary)',
                                        borderColor: 'var(--app-border)',
                                        color: 'var(--app-text-primary)',
                                    }}
                                >
                                    {[50, 100, 500, 1000].map((value) => (
                                        <SelectItem
                                            key={value}
                                            value={String(value)}
                                            className="text-xs focus:bg-[var(--app-primary)] focus:text-white"
                                        >
                                            {value}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </div>

                        {/* Auto-refresh toggle */}
                        <Tooltip label={autoRefresh ? 'Pause auto-refresh' : 'Resume auto-refresh'}>
                        <button
                            onClick={() => setAutoRefresh(!autoRefresh)}
                            className="cursor-pointer flex items-center gap-2 px-2 py-1.5 rounded-md text-xs font-medium transition-all hover:scale-105 active:scale-95 focus:outline-none focus:ring-2 focus:ring-opacity-50 shadow-sm"
                            style={{
                                backgroundColor: autoRefresh ? 'var(--app-primary)' : 'var(--app-secondary)',
                                color: autoRefresh ? '#ffffff' : 'var(--app-text-muted)',
                            }}
                            aria-label={autoRefresh ? 'Pause auto-refresh' : 'Resume auto-refresh'}
                        >
                            {autoRefresh ? <Pause size={12}/> : <Play size={12}/>}
                            {/* {autoRefresh ? 'Pause' : 'Play'} */}
                        </button>
                        </Tooltip>

                        {/* Refresh button */}
                        <Tooltip label="Refresh logs">
                        <button
                            onClick={() => fetchLogs()}
                            disabled={loading}
                            className="cursor-pointer flex items-center gap-2 px-2 py-1.5 rounded-md text-xs font-medium transition-all hover:scale-105 hover:shadow-md active:scale-95 disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:scale-100 focus:outline-none focus:ring-2 focus:ring-opacity-50 shadow-sm"
                            style={{
                                backgroundColor: 'var(--app-primary-light)',
                                color: '#ffffff',
                            }}
                            aria-label="Refresh logs"
                        >
                            <RefreshCw size={12} className={loading ? 'animate-spin' : ''}/>

                        </button>
                        </Tooltip>

                        {/* Word wrap toggle */}
                        <Tooltip label={wrapLines ? 'Disable line wrapping' : 'Enable line wrapping'}>
                        <button
                            onClick={() => setWrapLines(!wrapLines)}
                            className="cursor-pointer flex items-center gap-2 px-2 py-1.5 rounded-md text-xs font-medium transition-all hover:scale-105 active:scale-95 focus:outline-none focus:ring-2 focus:ring-opacity-50 shadow-sm"
                            style={{
                                backgroundColor: wrapLines ? 'var(--app-primary)' : 'var(--app-secondary)',
                                color: wrapLines ? '#ffffff' : 'var(--app-text-muted)',
                            }}
                            aria-label={wrapLines ? 'Disable line wrapping' : 'Enable line wrapping'}
                        >
                            <WrapText size={12}/>
                        </button>
                        </Tooltip>
                        <DropdownMenu.Root>
                            <Tooltip label="Copy displayed lines or the entire log">
                            <DropdownMenu.Trigger asChild>
                                <button type="button" disabled={actionBusy} className={actionClass} style={actionStyle} aria-label="Copy all">
                                    <Copy size={14}/><ChevronDown size={12}/>
                                </button>
                            </DropdownMenu.Trigger>
                            </Tooltip>
                            <DropdownMenu.Portal>
                                <DropdownMenu.Content align="start" sideOffset={6}
                                    className="z-[150] min-w-48 rounded-md border p-1 shadow-lg text-xs"
                                    style={{backgroundColor: 'var(--app-bg-secondary)', borderColor: 'var(--app-border)', color: 'var(--app-text-primary)'}}>
                                    <DropdownMenu.Item disabled={!filteredLogs.length}
                                        onSelect={() => void runLogAction('visible')}
                                        className="px-3 py-2 rounded outline-none cursor-pointer focus:bg-[var(--app-primary)] focus:text-white data-[disabled]:opacity-50 data-[disabled]:pointer-events-none">
                                        Copy displayed lines ({filteredLogs.length})
                                    </DropdownMenu.Item>
                                    <DropdownMenu.Item onSelect={() => void runLogAction('all')}
                                        className="px-3 py-2 rounded outline-none cursor-pointer focus:bg-[var(--app-primary)] focus:text-white">
                                        Copy entire log
                                    </DropdownMenu.Item>
                                </DropdownMenu.Content>
                            </DropdownMenu.Portal>
                        </DropdownMenu.Root>
                        <Tooltip label="Open the full log in your default application">
                        <button type="button" disabled={actionBusy} onClick={() => void runLogAction('open')}
                                className={actionClass} style={actionStyle} aria-label="Open log file">
                            <ExternalLink size={14}/>
                        </button>
                        </Tooltip>
                    </div>

                    {/* Line count or match count */}
                    <div className="flex items-center gap-2">
                        {searchQuery ? (
                            <>
                                <Search size={14} style={{color: 'var(--app-text-muted)'}}/>
                                <span className="text-xs font-medium px-2 py-0.5 rounded" style={{
                                    backgroundColor: filteredLogs.length > 0 ? 'var(--app-primary)' : 'var(--app-secondary)',
                                    color: filteredLogs.length > 0 ? '#ffffff' : 'var(--app-text-muted)',
                                }}>
                  {filteredLogs.length}
                </span>
                            </>
                        ) : (
                            <>
                                <FileText size={14} style={{color: 'var(--app-text-muted)'}}/>
                                <span className="text-xs font-medium" style={{color: 'var(--app-text-muted)'}}>
                  {logs.length}
                </span>
                            </>
                        )}
                    </div>
                </div>

                {/* Second row - Search */}
                <div className="px-3 pb-3">
                    <div className="relative flex items-center w-full">
                        <Search size={12} className="absolute left-2" style={{color: 'var(--app-text-muted)'}}/>
                        <input
                            type="text"
                            value={searchQuery}
                            onChange={(e) => setSearchQuery(e.target.value)}
                            placeholder="Search logs..."
                            className="w-full pl-7 pr-7 py-1 text-xs rounded-md border transition-all focus:outline-none focus:ring-2 focus:ring-opacity-50"
                            style={{
                                backgroundColor: 'var(--app-input-bg)',
                                borderColor: 'var(--app-input-border)',
                                color: 'var(--app-text-primary)',
                            }}
                        />
                        {searchQuery && (
                            <button
                                onClick={() => setSearchQuery('')}
                                className="cursor-pointer absolute right-2 hover:opacity-70 transition-opacity"
                                style={{color: 'var(--app-text-muted)'}}
                            >
                                <X size={12}/>
                            </button>
                        )}
                    </div>
                </div>
            </div>

            {actionMessage && (
                <div role={actionError ? 'alert' : 'status'} className="flex items-center gap-2 px-3 py-1.5 text-xs border-b"
                     style={{borderColor: 'var(--app-border)', color: actionError ? 'var(--app-warning)' : 'var(--app-success)'}}>
                    {!actionError && <Check size={12}/>}
                    <span className="flex-1 break-words">{actionMessage}</span>
                    <button type="button" aria-label="Dismiss log action message" onClick={() => setActionMessage('')}
                            className="p-1 cursor-pointer"><X size={12}/></button>
                </div>
            )}

            {/* Error message */}
            {error && (
                <div className="px-3 py-1.5 text-xs border-b" style={{
                    backgroundColor: 'var(--destructive)',
                    borderColor: 'var(--border)',
                    color: 'var(--destructive-foreground)',
                }}>
                    {error}
                </div>
            )}

            {/* Log content */}
            <ScrollArea
                ref={scrollContainerRef}
                onScroll={handleScroll}
                className="flex-1 min-h-0 m-3 p-3 rounded-lg font-mono text-xs leading-relaxed"
                style={{
                    backgroundColor: '#0d0d0d',
                    color: '#e8e8e8',
                    overflowY: 'auto',
                    overflowX: wrapLines ? 'hidden' : 'auto',
                }}
            >
                {logs.length === 0 ? (
                    <div className="flex flex-col items-center justify-center h-full gap-2"
                         style={{color: 'var(--muted-foreground)'}}>
                        <FileText size={32} opacity={0.3}/>
                        <span className="text-sm">No logs available</span>
                    </div>
                ) : filteredLogs.length === 0 ? (
                    <div className="flex flex-col items-center justify-center h-full gap-2"
                         style={{color: 'var(--muted-foreground)'}}>
                        <Search size={32} opacity={0.3}/>
                        <span className="text-sm">No matches found for "{searchQuery}"</span>
                    </div>
                ) : (
                    <div className="space-y-0.5">
                        {filteredLogs.map((line, index) => {
                            const parsed = parseLogLine(line);
                            return (
                                <div
                                    key={index}
                                    className="hover:bg-white/5 px-2 py-1 rounded transition-colors"
                                    style={{
                                        whiteSpace: wrapLines ? 'pre-wrap' : 'pre',
                                        wordBreak: wrapLines ? 'break-word' : 'normal',
                                    }}
                                >
                                    <span
                                        className="opacity-40 text-[11px]">{highlightMatch(parsed.timestamp, searchQuery)}</span>
                                    <span className={`mx-2 ${getLevelColor(parsed.level)} font-semibold text-[11px]`}>
                    [{highlightMatch(parsed.level, searchQuery)}]
                  </span>
                                    <span
                                        className="opacity-50 text-[11px]">[{highlightMatch(parsed.logger, searchQuery)}]</span>
                                    <span
                                        className="ml-2 text-[11px]">{highlightMatch(parsed.message, searchQuery)}</span>
                                    {parsed.extra && (
                                        <span className="ml-2 text-[11px] text-cyan-400/70">{highlightMatch(parsed.extra, searchQuery)}</span>
                                    )}
                                </div>
                            );
                        })}
                        <div ref={logEndRef}/>
                    </div>
                )}
            </ScrollArea>
        </div>
    );
};
