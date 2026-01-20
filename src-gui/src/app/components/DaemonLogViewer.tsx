/**
 * React component for displaying daemon logs in the GUI
 * 
 * Reads log files directly from the filesystem using Tauri commands.
 */

import React, { useState, useEffect, useRef } from 'react';
import { invoke } from '@tauri-apps/api/core';

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
        return 'text-gray-500';
      case 'INFO':
        return 'text-blue-500';
      case 'WARNING':
        return 'text-yellow-500';
      case 'ERROR':
        return 'text-red-500';
      case 'CRITICAL':
        return 'text-red-700 font-bold';
      default:
        return 'text-gray-400';
    }
  };

  return (
    <div className="daemon-log-viewer flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-1.5 border-b" style={{ borderColor: 'var(--border)' }}>
        <div className="flex items-center gap-3">
          {/* Number of lines selector */}
          <div className="flex items-center gap-1.5">
            <label className="text-xs" style={{ color: 'var(--muted-foreground)' }}>Righe:</label>
            <select
              value={numLines}
              onChange={(e) => {
                const value = parseInt(e.target.value);
                setNumLines(value);
                fetchLogs(value);
              }}
              className="border rounded px-1.5 py-0.5 text-xs"
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
          <label className="flex items-center gap-1.5 text-xs" style={{ color: 'var(--muted-foreground)' }}>
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={(e) => setAutoRefresh(e.target.checked)}
              className="rounded w-3 h-3"
            />
            Auto
          </label>

          {/* Action buttons */}
          <button
            onClick={() => fetchLogs()}
            disabled={loading}
            className="px-2 py-0.5 rounded text-xs transition-opacity hover:opacity-80 disabled:opacity-50"
            style={{
              backgroundColor: 'var(--primary)',
              color: 'var(--primary-foreground)',
            }}
          >
            {loading ? '...' : '↻'}
          </button>
        </div>
        
        <span className="text-xs" style={{ color: 'var(--muted-foreground)' }}>Totale: {logs.length}</span>
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
      <div className="flex-1 overflow-auto p-2 font-mono text-xs" style={{ 
        backgroundColor: '#0a0a0a',
        color: '#e5e5e5',
      }}>
        {logs.length === 0 ? (
          <div className="text-center py-4" style={{ color: 'var(--muted-foreground)' }}>
            Nessun log disponibile
          </div>
        ) : (
          <div>
            {logs.map((line, index) => {
              const parsed = parseLogLine(line);
              return (
                <div key={index} className="leading-tight hover:bg-white/5 px-1 py-0.5 rounded-sm">
                  <span className="opacity-50 text-[10px]">{parsed.timestamp}</span>
                  <span className={`mx-1 ${getLevelColor(parsed.level)} text-[10px]`}>
                    [{parsed.level}]
                  </span>
                  <span className="opacity-60 text-[10px]">[{parsed.logger}]</span>
                  <span className="ml-1">{parsed.message}</span>
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

