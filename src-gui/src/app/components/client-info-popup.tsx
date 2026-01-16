import { motion, AnimatePresence } from 'motion/react';

interface ClientInfoPopupProps {
  uid?: string;
  show: boolean;
  clientRect?: DOMRect;
  onMouseEnter?: () => void;
  onMouseLeave?: () => void;
}

export function ClientInfoPopup({ uid, show, clientRect, onMouseEnter, onMouseLeave }: ClientInfoPopupProps) {
  if (!uid || !clientRect) return null;

  // Calculate popup position above the client, closer
  const popupStyle = {
    position: 'fixed' as const,
    top: `${clientRect.top}px`,
    left: `${clientRect.left}px`,
    // transform: 'translate(-50%, -100%)',
  };

  return (
    <AnimatePresence>
      {show && (
        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: 10 }}
          transition={{ duration: 0.2 }}
          style={popupStyle}
          className="z-[100]"
          onMouseEnter={onMouseEnter}
          onMouseLeave={onMouseLeave}
        >
          <div 
            className="px-2 py-1 rounded-lg border shadow-2xl backdrop-blur-sm pointer-events-auto select-text"
            style={{ 
              backgroundColor: 'var(--app-card-bg)',
              borderColor: 'var(--app-primary)',
              color: 'var(--app-text-primary)',
            width: '300px',
            }}
          >
            <p className="text-sm font-semibold mb-2 select-none" style={{ color: 'var(--app-primary)' }}>Client UID</p>
            <p className="text-xs font-mono break-all max-w-xs select-text cursor-text" style={{ color: 'var(--app-text-muted)' }}>{uid}</p>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
