/**
 * Dialog component that wraps DaemonLogViewer
 * 
 * Controlled modal dialog for showing daemon logs
 */

import React from 'react';
import { motion, AnimatePresence } from 'motion/react';
import { DaemonLogViewer } from './DaemonLogViewer';
import { X } from 'lucide-react';

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
          <div className="absolute inset-0 backdrop-blur-sm" style={{ backgroundColor: 'rgba(0, 0, 0, 0.6)' }} />
          
          {/* Dialog Content */}
          <motion.div 
            initial={{ scale: 0.95, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            exit={{ scale: 0.95, opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="relative rounded-md shadow-2xl w-full h-full max-w-5xl max-h-[90vh] flex flex-col border"
            style={{
              backgroundColor: 'var(--card)',
              borderColor: 'var(--border)',
            }}
            onClick={(e) => e.stopPropagation()}
          >
            {/* Dialog Header */}
            <div className="flex items-center justify-between px-3 py-2 border-b" style={{ borderColor: 'var(--border)' }}>
              <div></div>
              
              {/* Close button */}
              <button
                onClick={onClose}
                className="p-0.5 rounded hover:opacity-70 transition-opacity"
                style={{ 
                  color: 'var(--muted-foreground)',
                }}
                aria-label="Chiudi"
              >
                <X size={16} />
              </button>
            </div>

            {/* Dialog Body - Log Viewer */}
            <div className="flex-1 overflow-hidden">
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
