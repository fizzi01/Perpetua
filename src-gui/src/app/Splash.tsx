/*
 * Perpetua - open-source and cross-platform KVM software.
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

export function SplashScreen() {

  var r = document.querySelector(':root') as HTMLElement;
  r.style.setProperty('--border-radius', '14px');

  return (
    <div data-tauri-drag-region
        className="min-h-screen w-full flex items-center justify-center" 
        style={{ 
            backgroundColor: 'var(--color-woodsmoke-950)',
            borderColor: 'var(--app-border)' 
            }}>
      <motion.div
        data-tauri-drag-region
        animate={{ 
          rotate: 360 
        }}
        transition={{
          duration: 2,
          repeat: Infinity,
          ease: "linear"
        }}
        style={{ 
            backgroundColor: 'var(--color-woodsmoke-950)',
            borderColor: 'var(--app-border)' 
        }}
      >
        <img 
          src="/logo_primary.svg" 
          alt="Perpetua Logo" 
          className="w-24 h-24 pointer-events-none"
        />
      </motion.div>
    </div>
  );
}