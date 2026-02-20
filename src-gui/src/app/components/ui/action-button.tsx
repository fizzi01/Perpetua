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

import {motion} from 'framer-motion';

interface ActionButtonProps {
    clicked: boolean;
    onClick: () => void;
    children: React.ReactNode;
}

export function ActionButton({clicked, onClick, children}: ActionButtonProps) {
    return (
        <motion.button
            whileHover={{scale: 1.02}}
            whileTap={{scale: 0.98}}
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