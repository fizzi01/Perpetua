import { motion, AnimatePresence } from 'motion/react';
import { CheckCircle, XCircle, AlertCircle, Info, X } from 'lucide-react';
import { useState } from 'react';

export interface Notification {
  id: string;
  type: 'success' | 'error' | 'warning' | 'info';
  message: string;
  description?: string;
}

interface InlineNotificationProps {
  notifications: Notification[];
  onDismiss?: (id: string) => void;
}

export function InlineNotification({ notifications, onDismiss }: InlineNotificationProps) {
  const [hoveredId, setHoveredId] = useState<string | null>(null);

  const getIcon = (type: string) => {
    switch (type) {
      case 'success':
        return <CheckCircle size={18} />;
      case 'error':
        return <XCircle size={18} />;
      case 'warning':
        return <AlertCircle size={18} />;
      case 'info':
        return <Info size={18} />;
      default:
        return <Info size={18} />;
    }
  };

  const getColors = (type: string) => {
    switch (type) {
      case 'success':
        return {
          bg: 'rgba(34, 197, 94, 0.15)',
          border: 'var(--app-success)',
          text: 'var(--app-success)',
        };
      case 'error':
        return {
          bg: 'rgba(239, 68, 68, 0.15)',
          border: 'var(--app-danger)',
          text: 'var(--app-danger)',
        };
      case 'warning':
        return {
          bg: 'rgba(245, 158, 11, 0.15)',
          border: 'var(--app-warning)',
          text: 'var(--app-warning)',
        };
      case 'info':
        return {
          bg: 'rgba(99, 102, 241, 0.15)',
          border: 'var(--app-primary)',
          text: 'var(--app-primary)',
        };
      default:
        return {
          bg: 'var(--app-card-bg)',
          border: 'var(--app-border)',
          text: 'var(--app-text-primary)',
        };
    }
  };

  // Show only the last 2 notifications
  const visibleNotifications = notifications.slice(-2);

  return (
    <div 
      className="sticky top-0 left-0 right-0 z-50 px-6 space-y-2 pointer-events-none"
    >
      <AnimatePresence mode="popLayout">
        {visibleNotifications.map((notification) => {
          const colors = getColors(notification.type);
          const isHovered = hoveredId === notification.id;
          
          return (
            <motion.div
              key={notification.id}
              initial={{ opacity: 0, y: -50, scale: 0.9 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: -50, scale: 0.9 }}
              transition={{ 
                type: "spring",
                stiffness: 300,
                damping: 30
              }}
              className="pointer-events-auto"
              onMouseEnter={() => setHoveredId(notification.id)}
              onMouseLeave={() => setHoveredId(null)}
            >
              <div
                className="p-3 rounded-lg border flex items-start gap-3 shadow-2xl backdrop-blur-xl relative overflow-hidden"
                style={{
                  backgroundColor: colors.bg,
                  borderColor: colors.border,
                  borderWidth: '2px',
                }}
              >
                {/* Gradient overlay on hover */}
                <motion.div
                  initial={{ opacity: 0 }}
                  animate={{ opacity: isHovered ? 0.1 : 0 }}
                  className="absolute inset-0"
                  style={{ backgroundColor: colors.border }}
                />
                
                <div style={{ color: colors.text }} className="mt-0.5 relative z-10">
                  {getIcon(notification.type)}
                </div>
                
                <div className="flex-1 min-w-0 relative z-10">
                  <p
                    className="text-sm font-semibold"
                    style={{ color: colors.text }}
                  >
                    {notification.message}
                  </p>
                  {notification.description && (
                    <p
                      className="text-xs mt-0.5"
                      style={{ color: 'var(--app-text-secondary)' }}
                    >
                      {notification.description}
                    </p>
                  )}
                </div>

                {/* Dismiss button */}
                {onDismiss && (
                  <motion.button
                    whileHover={{ scale: 1.1 }}
                    whileTap={{ scale: 0.9 }}
                    onClick={() => onDismiss(notification.id)}
                    className="relative z-10 opacity-60 hover:opacity-100 transition-opacity"
                    style={{ color: colors.text }}
                  >
                    <X size={16} />
                  </motion.button>
                )}

                {/* Progress bar */}
                <motion.div
                  initial={{ scaleX: 1 }}
                  animate={{ scaleX: 0 }}
                  transition={{ duration: 4, ease: "linear" }}
                  className="absolute bottom-0 left-0 right-0 h-1 origin-left"
                  style={{ backgroundColor: colors.border }}
                />
              </div>
            </motion.div>
          );
        })}
      </AnimatePresence>
    </div>
  );
}