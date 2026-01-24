/**
 * React component for displaying daemon logs in the GUI
 * 
 * Reads log files directly from the filesystem using Tauri commands.
 */

import React, { useState, useEffect, useRef, useMemo } from 'react';
import { invoke } from '@tauri-apps/api/core';
import { RefreshCw, FileText, Play, Pause, WrapText, Search, X } from 'lucide-react';

interface LogViewerProps {
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
  const logEndRef = useRef<HTMLDivElement>(null);

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
      setError(`Error reading logs: ${err}`);
    } finally {
      setLoading(false);
    }
  };

  // // Fetch all logs
  // const fetchAllLogs = async () => {
  //   setLoading(true);
  //   setError(null);

  //   try {
  //     const response = await invoke<LogResponse>('read_daemon_logs', {
  //       numLines: 0,
  //       all: true,
  //     });

  //     setLogs(response.logs);
  //   } catch (err) {
  //     setError(`Error reading logs: ${err}`);
  //   } finally {
  //     setLoading(false);
  //   }
  // };

  // Auto-scroll to bottom when new logs arrive
  const scrollToBottom = () => {
    logEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  // Initial load
  useEffect(() => {
    fetchLogs();
  }, []);

  // Auto-refresh
  useEffect(() => {
    if (!autoRefresh) return;

    const interval = setInterval(() => {
      fetchLogs();
    }, refreshInterval);

    return () => clearInterval(interval);
  }, [autoRefresh, refreshInterval, numLines]);

  // Scroll to bottom when logs update
  useEffect(() => {
    scrollToBottom();
  }, [logs]);

  // Parse log line to extract level and color
  const parseLogLine = (line: string) => {
    const match = line.match(/\[(.*?)\]\[(.*?)\]\[(.*?)\](.*)/);
    if (match) {
      const [, timestamp, level, logger, message] = match;
      return { timestamp, level, logger, message };
    }
    return { timestamp: '', level: 'INFO', logger: '', message: line };
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

  return (
    <div className="daemon-log-viewer flex flex-col h-full">
      {/* Header */}
      <div className="border-b" style={{ 
        borderColor: 'var(--border)',
        backgroundColor: 'var(--background)',
      }}>
        {/* First row - Controls */}
        <div className="flex items-center justify-between px-3 py-1.5">
          <div className="flex items-center gap-2">
            {/* Number of lines selector */}
            <div className="flex items-center gap-1.5">
              <label className="text-xs font-medium" style={{ color: 'var(--muted-foreground)' }}>Lines</label>
              <select
                value={numLines}
                onChange={(e) => {
                  const value = parseInt(e.target.value);
                  setNumLines(value);
                  fetchLogs(value);
                }}
                className="border rounded-md px-1.5 py-0.5 text-xs font-medium transition-all cursor-pointer hover:border-opacity-70 focus:outline-none focus:ring-2 focus:ring-opacity-50"
                style={{ 
                  backgroundColor: 'var(--input-background)',
                  borderColor: 'var(--border)',
                  color: 'var(--foreground)',
                }}
              >
                <option value={50}>50</option>
                <option value={100}>100</option>
                <option value={500}>500</option>
                <option value={1000}>1000</option>
              </select>
            </div>

            {/* Auto-refresh toggle */}
            <button
              onClick={() => setAutoRefresh(!autoRefresh)}
              className="cursor-pointer flex items-center gap-2 px-2 py-1.5 rounded-md text-xs font-medium transition-all hover:scale-105 active:scale-95 focus:outline-none focus:ring-2 focus:ring-opacity-50 shadow-sm"
              style={{
                backgroundColor: autoRefresh ? 'var(--primary)' : 'var(--muted)',
                color: autoRefresh ? 'var(--primary-foreground)' : 'var(--muted-foreground)',
              }}
              title={autoRefresh ? 'Auto-refresh enabled' : 'Auto-refresh disabled'}
            >
              {autoRefresh ? <Pause size={12} /> : <Play size={12} />}
              {autoRefresh ? 'Pause' : 'Play'}
            </button>

            {/* Refresh button */}
            <button
              onClick={() => fetchLogs()}
              disabled={loading}
              className="cursor-pointer flex items-center gap-1 px-2 py-1.5 rounded-md text-xs font-medium transition-all hover:scale-105 hover:shadow-md active:scale-95 disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:scale-100 focus:outline-none focus:ring-2 focus:ring-opacity-50 shadow-sm"
              style={{
                backgroundColor: 'var(--accent)',
                color: 'var(--accent-foreground)',
              }}
              title="Refresh logs"
            >
              <RefreshCw size={12} className={loading ? 'animate-spin' : ''} />
            </button>

            {/* Word wrap toggle */}
            <button
              onClick={() => setWrapLines(!wrapLines)}
              className="cursor-pointer flex items-center gap-1 px-2 py-1.5 rounded-md text-xs font-medium transition-all hover:scale-105 active:scale-95 focus:outline-none focus:ring-2 focus:ring-opacity-50 shadow-sm"
              style={{
                backgroundColor: wrapLines ? 'var(--primary)' : 'var(--muted)',
                color: wrapLines ? 'var(--primary-foreground)' : 'var(--muted-foreground)',
              }}
              title={wrapLines ? 'Line wrapping enabled' : 'Line wrapping disabled'}
            >
              <WrapText size={12} />
            </button>
          </div>

          {/* Line count or match count */}
          <div className="flex items-center gap-2">
            {searchQuery ? (
              <>
                <Search size={14} style={{ color: 'var(--muted-foreground)' }} />
                <span className="text-xs font-medium px-2 py-0.5 rounded" style={{
                  backgroundColor: filteredLogs.length > 0 ? 'var(--primary)' : 'var(--muted)',
                  color: filteredLogs.length > 0 ? 'var(--primary-foreground)' : 'var(--muted-foreground)',
                }}>
                  {filteredLogs.length}
                </span>
              </>
            ) : (
              <>
                <FileText size={14} style={{ color: 'var(--muted-foreground)' }} />
                <span className="text-xs font-medium" style={{ color: 'var(--muted-foreground)' }}>
                  {logs.length}
                </span>
              </>
            )}
          </div>
        </div>

        {/* Second row - Search */}
        <div className="px-3 pb-1.5">
          <div className="relative flex items-center w-full">
            <Search size={12} className="absolute left-2" style={{ color: 'var(--muted-foreground)' }} />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search logs..."
              className="w-full pl-7 pr-7 py-1 text-xs rounded-md border transition-all focus:outline-none focus:ring-2 focus:ring-opacity-50"
              style={{
                backgroundColor: 'var(--input-background)',
                borderColor: 'var(--border)',
                color: 'var(--foreground)',
              }}
            />
            {searchQuery && (
              <button
                onClick={() => setSearchQuery('')}
                className="cursor-pointer absolute right-2 hover:opacity-70 transition-opacity"
                style={{ color: 'var(--muted-foreground)' }}
              >
                <X size={12} />
              </button>
            )}
          </div>
        </div>
      </div>

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
      <div 
        className="flex-1 p-3 font-mono text-xs leading-relaxed" 
        style={{ 
          backgroundColor: '#0d0d0d',
          color: '#e8e8e8',
          overflow: wrapLines ? 'auto' : 'auto',
          overflowX: wrapLines ? 'hidden' : 'auto',
        }}
      >
        {logs.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full gap-2" style={{ color: 'var(--muted-foreground)' }}>
            <FileText size={32} opacity={0.3} />
            <span className="text-sm">No logs available</span>
          </div>
        ) : filteredLogs.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full gap-2" style={{ color: 'var(--muted-foreground)' }}>
            <Search size={32} opacity={0.3} />
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
                  <span className="opacity-40 text-[11px]">{highlightMatch(parsed.timestamp, searchQuery)}</span>
                  <span className={`mx-2 ${getLevelColor(parsed.level)} font-semibold text-[11px]`}>
                    [{highlightMatch(parsed.level, searchQuery)}]
                  </span>
                  <span className="opacity-50 text-[11px]">[{highlightMatch(parsed.logger, searchQuery)}]</span>
                  <span className="ml-2 text-[11px]">{highlightMatch(parsed.message, searchQuery)}</span>
                </div>
              );
            })}
            <div ref={logEndRef} />
          </div>
        )}
      </div>
    </div>
  );
};

