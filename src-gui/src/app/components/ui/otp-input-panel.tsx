import { motion, AnimatePresence } from 'motion/react';
import { Key, XCircle, CheckCircle2 } from 'lucide-react';
import { useState, useEffect } from 'react';

interface OtpInputPanelProps {
  /** Whether the panel is visible */
  isVisible: boolean;
  /** Callback when OTP is submitted */
  onSubmit: (otp: string) => void;
  /** Optional callback to cancel OTP input */
  onCancel?: () => void;
  /** Custom className */
  className?: string;
}

export function OtpInputPanel({
  isVisible,
  onSubmit,
  onCancel,
  className = '',
}: OtpInputPanelProps) {
  const [otpInput, setOtpInput] = useState('');
  const [isProcessing, setIsProcessing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = () => {
    // Validate OTP
    if (otpInput.length === 0 || otpInput.length < 6) {
      setError('Please enter a valid 6-digit OTP code');
      return;
    }

    setError(null);
    setIsProcessing(true);
    onSubmit(otpInput);
    
    // Reset after animation
    // setTimeout(() => {
    //   setIsProcessing(false);
    //   setOtpInput('');
    // }, 1500);
  };

  const handleCancel = () => {
    setOtpInput('');
    setError(null);
    onCancel?.();
  };

  // Reset state when panel is closed
  useEffect(() => {
    if (!isVisible && (otpInput || error || isProcessing)) {
      setOtpInput('');
      setError(null);
      setIsProcessing(false);
    }
  }, [isVisible]);

  if (!isVisible) {
    return null;
  }

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
                <Key size={24} style={{ color: 'white' }} />
              </motion.div>
              <div className="flex-1">
                <h3 className="text-base font-bold" style={{ color: 'var(--app-text-primary)' }}>
                  Authentication Required
                </h3>
                <p className="text-xs mt-1" style={{ color: 'var(--app-text-muted)' }}>
                  Enter the 6-digit OTP code
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
            Please enter the one-time password displayed on the server:
          </motion.p>

          {/* OTP Input */}
          <motion.div
            initial={{ opacity: 0, scale: 0.95 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ delay: 0.3 }}
            className="mb-4"
          >
            <input
              type="text"
              placeholder="000000"
              maxLength={6}
              value={otpInput}
              onChange={(e) => {
                setOtpInput(e.target.value.replace(/\D/g, ''));
                setError(null); // Clear error when user types
              }}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !isProcessing) {
                  handleSubmit();
                }
              }}
              disabled={isProcessing}
              className="w-full p-4 rounded-lg focus:outline-none text-center text-2xl font-bold tracking-widest transition-all"
              style={{
                backgroundColor: 'var(--app-input-bg)',
                border: error ? '2px solid var(--app-danger)' : '2px solid var(--app-input-border)',
                color: 'var(--app-text-primary)'
              }}
              onFocus={(e) => !error && (e.currentTarget.style.borderColor = 'var(--app-primary)')}
              onBlur={(e) => !error && (e.currentTarget.style.borderColor = 'var(--app-input-border)')}
              autoFocus
            />

            {/* Error Message */}
            <AnimatePresence>
              {error && (
                <motion.p
                  initial={{ opacity: 0, y: -10 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -10 }}
                  className="text-xs mt-2 flex items-center gap-1"
                  style={{ color: 'var(--app-danger)' }}
                >
                  <XCircle size={14} />
                  {error}
                </motion.p>
              )}
            </AnimatePresence>
          </motion.div>

          {/* Action Buttons */}
          <div className="space-y-2">
            {/* Submit Button */}
            <motion.button
              whileHover={{ scale: isProcessing ? 1 : 1.02 }}
              whileTap={{ scale: isProcessing ? 1 : 0.98 }}
              onClick={handleSubmit}
              disabled={isProcessing}
              className="cursor-pointer w-full p-3 rounded-lg transition-all flex items-center justify-center gap-2"
              style={{
                backgroundColor: isProcessing ? 'var(--app-success)' : 'var(--app-primary)',
                color: 'white',
                opacity: isProcessing ? 0.8 : 1,
                cursor: isProcessing ? '' : 'pointer'
              }}
            >
              {isProcessing ? (
                <>
                  <motion.div
                    animate={{ rotate: 360 }}
                    transition={{ duration: 1, repeat: Infinity, ease: "linear" }}
                  >
                    <Key size={18} />
                  </motion.div>
                  <span className="text-sm font-medium">Authenticating...</span>
                </>
              ) : (
                <>
                  <CheckCircle2 size={18} />
                  <span className="text-sm font-medium">Connect</span>
                </>
              )}
            </motion.button>

            {/* Cancel Button */}
            {onCancel && (
              <motion.button
                whileHover={{ scale: 1.02 }}
                whileTap={{ scale: 0.98 }}
                onClick={handleCancel}
                disabled={isProcessing}
                className="cursor-pointer w-full p-3 rounded-lg transition-all flex items-center justify-center gap-2 border"
                style={{
                  backgroundColor: 'var(--app-bg-tertiary)',
                  borderColor: 'var(--app-border)',
                  color: 'var(--app-text-secondary)',
                  opacity: isProcessing ? 0.5 : 1,
                  cursor: isProcessing ? '' : 'pointer'
                }}
              >
                <XCircle size={18} />
                <span className="text-sm font-medium">Cancel</span>
              </motion.button>
            )}
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
