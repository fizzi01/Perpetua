import { motion, AnimatePresence } from 'motion/react';
import { Server, Wifi, Globe, Hash, CheckCircle2, XCircle } from 'lucide-react';
import { useState } from 'react';
import { ServerFound } from '../../api/Interface';
import { CopyableBadge, abbreviateText } from './copyable-badge';

interface ServerSelectionPanelProps {
  /** Server choice data containing available servers */
  serverChoice: ServerFound[] | null;
  /** Callback when a server is selected */
  onServerSelect: (serverUid: string) => void;
  /** Whether the panel is visible */
  isVisible: boolean;
  /** Optional callback to cancel selection */
  onCancel?: () => void;
  /** Custom className */
  className?: string;
}

export function ServerSelectionPanel({
  serverChoice,
  onServerSelect,
  isVisible,
  onCancel,
  className = '',
}: ServerSelectionPanelProps) {
  const [selectedUid, setSelectedUid] = useState<string | null>(null);
  const [isProcessing, setIsProcessing] = useState(false);

  if (!isVisible || !serverChoice || serverChoice.length === 0) {
    return null;
  }

  const handleSelect = (serverUid: string) => {
    setSelectedUid(serverUid);
    setIsProcessing(true);
    onServerSelect(serverUid);
    
    // Reset after animation
    setTimeout(() => {
      setIsProcessing(false);
      setSelectedUid(null);
    }, 1500);
  };

  return (
    <AnimatePresence mode="wait">
      {isVisible && (
        <motion.div
          initial={{ opacity: 0, y: -20, scale: 0.95 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          exit={{ opacity: 0, y: -20, scale: 0.95 }}
          transition={{ duration: 0.3, ease: "easeOut" }}
          className={`p-5 rounded-xl border-2 backdrop-blur-sm ${className}`}
          style={{
            backgroundColor: 'var(--app-card-bg)',
            borderColor: 'var(--app-primary)',
            boxShadow: '0 8px 32px rgba(0, 0, 0, 0.12)',
          }}
        >
          {/* Header Section */}
          <motion.div
            initial={{ opacity: 0, y: -10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.1 }}
            className="mb-5"
          >
            <div className="flex items-center gap-3 mb-3">
              <motion.div
                animate={{
                  scale: [1, 1.1, 1],
                }}
                transition={{
                  duration: 2,
                  repeat: Infinity,
                  ease: "easeInOut"
                }}
                className="w-12 h-12 rounded-lg flex items-center justify-center"
                style={{ backgroundColor: 'var(--app-primary)' }}
              >
                <Wifi size={24} style={{ color: 'white' }} />
              </motion.div>
              <div className="flex-1">
                <h3 className="text-base font-bold" style={{ color: 'var(--app-text-primary)' }}>
                  Multiple Servers Detected
                </h3>
                <p className="text-xs mt-1" style={{ color: 'var(--app-text-muted)' }}>
                  {serverChoice.length} server{serverChoice.length > 1 ? 's' : ''} found on your network
                </p>
              </div>
            </div>

            {/* Divider */}
            <motion.div
              initial={{ scaleX: 0 }}
              animate={{ scaleX: 1 }}
              transition={{ delay: 0.2, duration: 0.3 }}
              className="h-px w-full"
              style={{ 
                backgroundColor: 'var(--app-border)',
                opacity: 0.5,
                transformOrigin: 'left'
              }}
            />
          </motion.div>

          {/* Info Text */}
          <motion.p
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ delay: 0.25 }}
            className="text-sm mb-4"
            style={{ color: 'var(--app-text-secondary)' }}
          >
            Select which server you'd like to connect to:
          </motion.p>

          {/* Servers List */}
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ delay: 0.3 }}
            className="space-y-2.5 max-h-80 overflow-y-auto pr-1 mb-4"
            style={{
              scrollbarWidth: 'thin',
              scrollbarColor: 'var(--app-primary) transparent'
            }}
          >
            {serverChoice.map((server, index) => (
              <ServerCard
                key={server.uid}
                server={server}
                index={index}
                isSelected={selectedUid === server.uid}
                isProcessing={isProcessing && selectedUid === server.uid}
                onSelect={handleSelect}
              />
            ))}
          </motion.div>

          {/* Cancel Button */}
          {onCancel && (
            <>
              {/* Divider */}
              <motion.div
                initial={{ scaleX: 0 }}
                animate={{ scaleX: 1 }}
                transition={{ duration: 0.3 }}
                className="h-px w-full mb-4"
                style={{ 
                  backgroundColor: 'var(--app-border)',
                  opacity: 0.5,
                  transformOrigin: 'left'
                }}
              />
              
              <motion.button
                whileHover={{ scale: 1.02 }}
                whileTap={{ scale: 0.98 }}
                onClick={onCancel}
                className="w-full p-3 rounded-lg transition-all flex items-center justify-center gap-2 border"
                style={{
                  backgroundColor: 'var(--app-bg-tertiary)',
                  borderColor: 'var(--app-border)',
                  color: 'var(--app-text-secondary)'
                }}
              >
                <XCircle size={18} />
                <span className="text-sm font-medium">Cancel</span>
              </motion.button>
            </>
          )}
        </motion.div>
      )}
    </AnimatePresence>
  );
}

interface ServerCardProps {
  server: ServerFound;
  index: number;
  isSelected: boolean;
  isProcessing: boolean;
  onSelect: (uid: string) => void;
}

function ServerCard({ server, index, isSelected, isProcessing, onSelect }: ServerCardProps) {
  const [isHovered, setIsHovered] = useState(false);

  return (
    <motion.button
      key={index}
      whileTap={{ scale: 0.98 }}
      onClick={() => !isProcessing && onSelect(server.uid)}
      onHoverStart={() => setIsHovered(true)}
      onHoverEnd={() => setIsHovered(false)}
      disabled={isProcessing}
      className="w-full p-3.5 rounded-lg border transition-all cursor-pointer relative overflow-hidden"
      style={{
        backgroundColor: isSelected
          ? 'var(--app-primary)'
          : isHovered
          ? 'var(--app-bg-secondary)'
          : 'var(--app-bg-tertiary)',
        borderColor: isSelected ? 'var(--app-primary)' : 'var(--app-card-border)',
        borderWidth: isSelected ? '2px' : '1px',
        boxShadow: isSelected 
          ? '0 4px 12px rgba(0, 0, 0, 0.15)' 
          : isHovered 
          ? '0 2px 8px rgba(0, 0, 0, 0.08)' 
          : 'none',
      }}
    >
      {/* Selection Indicator */}
      <AnimatePresence>
        {isSelected && (
          <motion.div
            initial={{ scale: 0, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            exit={{ scale: 0, opacity: 0 }}
            className="absolute top-2 right-2"
          >
            {isProcessing ? (
              <motion.div
                animate={{ rotate: 360 }}
                transition={{ duration: 1, repeat: Infinity, ease: "linear" }}
              >
                <Wifi size={20} style={{ color: 'white' }} />
              </motion.div>
            ) : (
              <CheckCircle2 size={20} style={{ color: 'white' }} />
            )}
          </motion.div>
        )}
      </AnimatePresence>

      <div className="flex items-start gap-4">
        {/* Server Icon */}
        <motion.div
          animate={{
            backgroundColor: isSelected
              ? 'rgba(255, 255, 255, 0.2)'
              : 'var(--app-bg-tertiary)',
          }}
          className="w-12 h-12 rounded-lg flex items-center justify-center flex-shrink-0"
        >
          <Server
            size={24}
            style={{
              color: isSelected ? 'white' : 'var(--app-text-muted)',
            }}
          />
        </motion.div>

        {/* Server Info */}
        <div className="flex-1 text-left space-y-2">
          {/* Hostname */}
          <div className="flex items-center gap-2">
            <Globe
              size={14}
              style={{
                color: isSelected ? 'rgba(255, 255, 255, 0.8)' : 'var(--app-text-muted)',
              }}
            />
            <span
              className="text-sm font-bold"
              style={{
                color: isSelected ? 'white' : 'var(--app-text-primary)',
              }}
            >
              {server.hostname}
            </span>
          </div>

          {/* Address and Port */}
          <div className="flex items-center gap-2">
            <Wifi
              size={14}
              style={{
                color: isSelected ? 'rgba(255, 255, 255, 0.8)' : 'var(--app-text-muted)',
              }}
            />
            <span
              className="text-xs font-mono"
              style={{
                color: isSelected ? 'rgba(255, 255, 255, 0.9)' : 'var(--app-text-secondary)',
              }}
            >
              {server.address}:{server.port}
            </span>
          </div>

          {/* Server UID Badge */}
          <div className="flex items-center gap-2 mt-2">
            <Hash
              size={14}
              style={{
                color: isSelected ? 'rgba(255, 255, 255, 0.8)' : 'var(--app-text-muted)',
              }}
            />
            <div onClick={(e) => e.stopPropagation()}>
              <CopyableBadge
                fullText={server.uid}
                displayText={abbreviateText(server.uid, 6, 4)}
                label=""
                titleText={`Server UID: ${server.uid}`}
                style={{
                  backgroundColor: isSelected
                    ? 'rgba(255, 255, 255, 0.2)'
                    : 'var(--app-bg-tertiary)',
                  borderColor: isSelected ? 'rgba(255, 255, 255, 0.3)' : 'var(--app-border)',
                  color: isSelected ? 'white' : 'var(--app-text-muted)',
                }}
              />
            </div>
          </div>
        </div>
      </div>

      {/* Hover Effect Gradient */}
      <AnimatePresence>
        {isHovered && !isSelected && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="absolute inset-0 pointer-events-none"
            style={{
              background: 'linear-gradient(90deg, transparent, var(--app-primary-alpha), transparent)',
              opacity: 0.05,
            }}
          />
        )}
      </AnimatePresence>
    </motion.button>
  );
}
