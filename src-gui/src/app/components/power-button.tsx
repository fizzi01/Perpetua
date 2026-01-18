import { motion } from 'motion/react';
import { Power } from 'lucide-react';
import { CopyableBadge, abbreviateText } from './copyable-badge';

type PowerButtonStatus = 'stopped' | 'running' | 'pending' | 'connecting';

interface PowerButtonProps {
  /** Current status of the service */
  status: PowerButtonStatus;
  /** Callback when button is clicked */
  onClick: () => void;
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
  const isActive = isRunning || isConnecting;

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
          cursor: isPending || isConnecting ? 'not-allowed' : 'pointer',
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
      {isRunning && uid && (
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
