import { motion, AnimatePresence } from 'motion/react';
import { useState } from 'react';

export interface CopyableBadgeProps {
  /** Full text to copy to clipboard */
  fullText: string;
  /** Display text (can be abbreviated) */
  displayText?: string;
  /** Label to show before the text */
  label?: string;
    /** Title text on hover */
    titleText?: string;
  /** Callback when text is copied */
  onCopy?: (text: string) => void;
  /** Show copy icon */
  showIcon?: boolean;
  /** Custom className */
  className?: string;
  /** Custom styles */
  style?: React.CSSProperties;
}

export function CopyableBadge({
  fullText,
  displayText,
  label,
  titleText,
  onCopy,
  showIcon = true,
  className = '',
  style = {},
}: CopyableBadgeProps) {
  const [isCopied, setIsCopied] = useState(false);

  const handleCopy = () => {
    navigator.clipboard.writeText(fullText);
    setIsCopied(true);
    onCopy?.(fullText);
    
    setTimeout(() => {
      setIsCopied(false);
    }, 1500);
  };

  const textToDisplay = displayText || fullText;

  const title = titleText || `Click to copy: ${fullText}`;

  return (
    <motion.button
      initial={{ 
        // opacity: 0, 
        // y: 5,
        backgroundColor: 'var(--app-bg-tertiary)',
        borderColor: 'var(--app-border)',
      }}
      animate={{ 
        // opacity: 1, 
        // y: 0,
        backgroundColor: isCopied ? 'var(--app-success-bg)' : 'var(--app-bg-tertiary)',
        borderColor: isCopied ? 'var(--app-success)' : 'var(--app-border)',
      }}
      whileHover={{ scale: 1.05 }}
      whileTap={{ scale: 0.95 }}
      onClick={handleCopy}
      className={`px-3 py-1 rounded-md flex items-center gap-2 transition-all ${className}`}
      style={{
        color: isCopied ? 'var(--app-success)' : 'var(--app-text-muted)',
        border: isCopied ? '1px solid var(--app-success)' : '1px solid var(--app-border)',
        ...style,
      }}
      title={title}
    >
      <AnimatePresence mode="wait">
        {isCopied ? (
          <motion.span
            key="copied"
            initial={{ opacity: 0, scale: 0.8 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.8 }}
            className="text-xs font-semibold"
          >
            Copied!
          </motion.span>
        ) : (
          <motion.span
            key="text"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="text-xs font-mono"
          >
            {label && `${label}: `}
            {textToDisplay}
          </motion.span>
        )}
      </AnimatePresence>
      {showIcon && (
        <AnimatePresence mode="wait">
          {isCopied ? (
            <motion.svg
              key="check"
              initial={{ scale: 0, rotate: -180 }}
              animate={{ scale: 1, rotate: 0 }}
              exit={{ scale: 0, rotate: 180 }}
              width="12"
              height="12"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
            >
              <polyline points="20 6 9 17 4 12"></polyline>
            </motion.svg>
          ) : (
            <motion.svg
              key="copy"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              width="12"
              height="12"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
            >
              <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
              <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
            </motion.svg>
          )}
        </AnimatePresence>
      )}
    </motion.button>
  );
}

/**
 * Utility function to abbreviate long strings
 * @param text - The text to abbreviate
 * @param startChars - Number of characters to show at start (default: 8)
 * @param endChars - Number of characters to show at end (default: 4)
 * @returns Abbreviated string like "1a2b3c4d...xyz9"
 */
export function abbreviateText(text: string, startChars: number = 8, endChars: number = 4): string {
  if (text.length <= startChars + endChars + 3) {
    return text;
  }
  return `${text.slice(0, startChars)}...${text.slice(-endChars)}`;
}
