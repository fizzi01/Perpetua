/*
 * Perpatua - open-source and cross-platform KVM software.
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
 * Dialog component that wraps DaemonLogViewer
 * 
 * Controlled modal dialog for showing daemon logs
 */

import React from 'react';
import { motion, AnimatePresence } from 'motion/react';
import { DaemonLogViewer } from './DaemonLogViewer';
import { X, FileText } from 'lucide-react';

interface DaemonLogDialogProps {
  isOpen: boolean;
  onClose: () => void;
}

export const DaemonLogDialog: React.FC<DaemonLogDialogProps> = ({ isOpen, onClose }) => {
  return (
    <AnimatePresence>
      {isOpen && (
        <motion.div 
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.2 }}
          className="fixed inset-0 z-50 flex items-center justify-center p-4"
          onClick={onClose}
        >
          {/* Backdrop */}
          <div data-tauri-drag-region className="absolute inset-0 backdrop-blur-md" style={{ backgroundColor: 'rgba(0, 0, 0, 0.7)' }} />
          
          {/* Dialog Content */}
          <motion.div 
            initial={{ scale: 0.96, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            exit={{ scale: 0.96, opacity: 0 }}
            transition={{ duration: 0.3, ease: 'easeInOut' }}
            className="relative rounded-lg shadow-2xl w-full h-full max-w-6xl max-h-[85vh] flex flex-col border"
            style={{
              backgroundColor: 'var(--card)',
              borderColor: 'var(--border)',
            }}
            onClick={(e) => e.stopPropagation()}
          >
            {/* Dialog Header */}
            <div data-tauri-drag-region
            className="flex items-center justify-between rounded-lg px-4 py-1 border-b backdrop-blur-sm" style={{ 
              borderColor: 'var(--border)',
              backgroundColor: 'var(--muted)',
            }}>
              <div className="flex items-center gap-2">
                <FileText size={16} style={{ color: 'var(--muted-foreground)' }} />
                <h2 className="font-medium text-sm" style={{ color: 'var(--foreground)' }}>
                  Logs
                </h2>
              </div>
              
              {/* Close button */}
              <button
                onClick={onClose}
                className="cursor-pointer p-1.5 rounded-md hover:bg-black/10 dark:hover:bg-white/10 active:scale-95 transition-all focus:outline-none focus:ring-2 focus:ring-opacity-50"
                style={{ 
                  color: 'var(--muted-foreground)',
                }}
                aria-label="Close"
              >
                <X size={16} />
              </button>
            </div>

            {/* Dialog Body - Log Viewer */}
            <div className="flex-1 rounded-lg overflow-hidden">
              <DaemonLogViewer 
                initialLines={100}
                refreshInterval={5000}
              />
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
};
