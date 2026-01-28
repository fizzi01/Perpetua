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

import { motion } from 'motion/react';
import { Power, X } from 'lucide-react';
import { CopyableBadge, abbreviateText } from './copyable-badge';

type PowerButtonStatus = 'stopped' | 'running' | 'pending' | 'connecting';

interface PowerButtonProps {
  /** Current status of the service */
  status: PowerButtonStatus;
  /** Callback when button is clicked */
  onClick: () => void;
  /** Callback when force stop is clicked (only shown when connecting) */
  onForceStop?: () => void;
  /** Label to show when stopped */
  stoppedLabel?: string;
  /** Label to show when running */
  runningLabel?: string;
  /** Label to show when pending */
  pendingLabel?: string;
  /** Label to show when connecting (only for client) */
  connectingLabel?: string;
  /** Optional UID to display when running */
  uid?: string;
  /** Optional custom className */
  className?: string;
}

export function PowerButton({
  status,
  onClick,
  onForceStop,
  stoppedLabel = 'Stopped',
  runningLabel = 'Running',
  pendingLabel = '',
  connectingLabel = 'Connecting',
  uid,
  className = '',
}: PowerButtonProps) {
  const isPending = status === 'pending';
  const isConnecting = status === 'connecting';
  const isRunning = status === 'running';
  const isActive = isRunning ;

  const getStatusLabel = () => {
    switch (status) {
      case 'pending':
        return pendingLabel;
      case 'connecting':
        return connectingLabel;
      case 'running':
        return runningLabel;
      case 'stopped':
      default:
        return stoppedLabel;
    }
  };

  return (
    <div className={`flex flex-col items-center ${className}`}>
      <div className="relative flex items-center justify-center">
        <motion.button
          whileHover={!isPending && !isConnecting ? { scale: 1.05 } : {}}
          whileTap={!isPending && !isConnecting ? { scale: 0.95 } : {}}
          onClick={onClick}
          disabled={isPending || isConnecting}
          className="w-24 h-24 rounded-full flex items-center justify-center transition-all duration-300 shadow-lg relative overflow-hidden"
          style={{
            backgroundColor: isActive ? 'var(--app-success)' : 'var(--app-bg-tertiary)',
            color: 'white',
            opacity: isPending || isConnecting ? 0.7 : 1,
            cursor: isPending || isConnecting ? '' : 'pointer',
          }}
        >
          {/* Pending/Connecting Animation */}
          {(isPending || isConnecting) && (
            <>
              {/* Spinner Animation */}
              <motion.div
                className="absolute inset-0 z-10"
                style={{
                  background: 'conic-gradient(from 0deg, transparent, rgba(255,255,255,0.4), transparent)',
                }}
                animate={{ rotate: 360 }}
                transition={{ duration: 1, repeat: Infinity, ease: 'linear' }}
              />
              {/* Pulse Effect */}
              <motion.div
                className="absolute inset-0 rounded-full"
                style={{
                  backgroundColor: 'rgba(255, 255, 255, 0.2)',
                }}
                animate={{ scale: [1, 1.2, 1], opacity: [0.5, 0, 0.5] }}
                transition={{ duration: 1.5, repeat: Infinity, ease: 'easeInOut' }}
              />
            </>
          )}
          
          {/* Running Animation */}
          {isRunning && !isPending && !isConnecting && (
            <motion.div
              className="absolute inset-0 opacity-30"
              style={{ backgroundColor: 'var(--app-success)' }}
              animate={{ scale: [1, 1.5, 1] }}
              transition={{ duration: 2, repeat: Infinity }}
            />
          )}
          
          {/* Power Icon */}
          <Power
            size={48}
            className="relative z-10"
            style={{ opacity: isPending || isConnecting ? 0.5 : 1 }}
          />
        </motion.button>

        {/* Force Stop Button */}
        {(isPending || isConnecting) && onForceStop && (
          <>
            {/* Subtle pulse backdrop */}
            <motion.div
              initial={{ opacity: 0, scale: 0.8 }}
              // animate={{ 
              //   opacity: [0.2, 0.35, 0.2],
              //   scale: [1, 1.2, 1]
              // }}
              exit={{ opacity: 0 }}
              transition={{
                opacity: { duration: 2, repeat: Infinity, ease: "easeInOut" },
                scale: { duration: 2, repeat: Infinity, ease: "easeInOut" }
              }}
              className="absolute w-10 h-10 rounded-full z-10"
              style={{
                backgroundColor: 'var(--app-danger)',
                top: '-5px',
                right: '-5px',
                filter: 'blur(6px)',
              }}
            />
            
            {/* Force stop button */}
            <motion.button
              initial={{ 
                opacity: 0,
                scale: 0,
                rotate: -180
              }}
              animate={{ 
                opacity: 1,
                scale: 1,
                rotate: 0
              }}
              exit={{ 
                opacity: 0,
                scale: 0,
                rotate: 180
              }}
              transition={{
                type: "spring",
                stiffness: 300,
                damping: 15,
                delay: 0.1
              }}
              whileHover={{ 
                scale: 1.1,
                rotate: 90,
                transition: { duration: 0.2 },
                backgroundColor: 'var(--app-danger-hover)'
              }}
              whileTap={{ 
                scale: 0.9,
                transition: { duration: 0.1 }
              }}
              onClick={onForceStop}
              className="cursor-pointer absolute w-9 h-9 rounded-full flex items-center justify-center z-20 border-2"
              style={{
                backgroundColor: 'var(--app-danger)',
                borderWidth: '0px',
                color: 'white',
                top: '-5px',
                right: '-5px',
                boxShadow: '0 4px 6px rgba(0, 0, 0, 0.3)',
              }}
              title="Force Stop"
            >
              <X size={20} strokeWidth={3} />
            </motion.button>
          </>
        )}
      </div>

      {/* Status Label */}
      <motion.p
        key={status}
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        className="mt-3 font-semibold"
        style={{ color: 'var(--app-text-primary)' }}
      >
        {getStatusLabel()}
      </motion.p>

      {/* UID Badge (optional) */}
      {(isRunning || isConnecting) && uid && (
        <CopyableBadge
          key={uid}
          fullText={uid}
          displayText={abbreviateText(uid)}
          label="UID"
          className="mt-2"
        />
      )}
    </div>
  );
}
