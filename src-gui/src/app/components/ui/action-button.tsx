import { motion } from 'framer-motion';

interface ActionButtonProps {
  clicked: boolean;
  onClick: () => void;
    children: React.ReactNode;
}

export function ActionButton({ clicked, onClick, children}: ActionButtonProps) {
  return (
    <motion.button
      whileHover={{ scale: 1.02 }}
      whileTap={{ scale: 0.98 }}
      onClick={onClick}
      className="cursor-pointer p-3 rounded-lg transition-all duration-300 flex flex-col items-center gap-1"
      style={{
        backgroundColor: clicked ? 'var(--app-secondary)' : 'var(--app-primary)',
        color: clicked ? 'var(--app-secondary-light)' : 'white'
      }}
    >
      {children}
    </motion.button>
  );
}