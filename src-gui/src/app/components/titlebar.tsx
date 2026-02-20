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

import {motion} from 'motion/react';
import {WindowTitlebar} from '../../tauri-controls/window-titlebar';
import {platform} from '@tauri-apps/plugin-os';

interface TitlebarProps {
    disabled?: boolean;
    mode: 'client' | 'server';
    onModeChange: (mode: 'client' | 'server') => void;
}

interface InnerBarProps extends TitlebarProps {
    justify_mode: "between" | "center";
}

export function InnerBar({disabled, mode, justify_mode, onModeChange}: InnerBarProps) {
    return (<div
        data-tauri-drag-region
        className={`titlebar w-full px-6 py-1.5 flex items-center justify-${justify_mode} border-b backdrop-blur-md`}
    >
        <div></div>
        {/* Mode Toggle */}
        <motion.div
            initial={{opacity: 0, scale: 0.95}}
            animate={{opacity: 1, scale: 1}}
            transition={{duration: 0.3}}
            className="titlebar-toggle w-[200px] rounded-md p-0.5 flex relative shadow-sm border h-full overflow-hidden"
        >
            {/* Liquid Glass Indicator */}
            <motion.div
                className="absolute top-0.5 bottom-0.5 left-0.5 rounded-sm backdrop-blur-xl shadow-md pointer-events-none"
                style={{
                    width: '96px',
                    background: 'rgba(255, 255, 255, 0.15)',
                    boxShadow: '0 4px 16px rgba(0, 0, 0, 0.1), inset 0 1px 0 rgba(255, 255, 255, 0.3)',
                }}
                animate={{
                    left: mode === 'client' ? '2px' : '102px',
                }}
                transition={{
                    type: 'spring',
                    stiffness: 300,
                    damping: 25,
                    mass: 0.6,
                }}
            />

            <button
                disabled={disabled}
                onClick={() => onModeChange('client')}
                className={`relative z-10 flex-1 py-1.5 px-4 rounded-sm font-medium text-xs tracking-wide h-full transition-all duration-200 ${disabled ? 'opacity-50' : 'cursor-pointer hover:scale-105 hover:text-white active:scale-95'}`}
                style={{
                    color: mode === 'client' ? 'var(--app-text-primary)' : 'var(--app-text-secondary)',
                }}
            >
                CLIENT
            </button>
            <button
                disabled={disabled}
                onClick={() => onModeChange('server')}
                className={`relative z-10 flex-1 py-1.5 px-4 rounded-sm font-medium text-xs tracking-wide h-full transition-all duration-200 ${disabled ? 'opacity-50' : 'cursor-pointer hover:scale-105 hover:text-white active:scale-95'}`}
                style={{
                    color: mode === 'server' ? 'var(--app-text-primary)' : 'var(--app-text-secondary)',
                }}
            >
                SERVER
            </button>
        </motion.div>
    </div>);
}


export function Titlebar({disabled, mode, onModeChange}: TitlebarProps) {

    const currentPlatform = platform();

    return (
        currentPlatform === "windows" ? (
            <WindowTitlebar className='titlebar w-full' windowControlsProps={{
                platform: 'windows',
                className: 'titlebar-system-group h-full border-b backdrop-blur-md',
            }}>
                <InnerBar disabled={disabled} mode={mode} justify_mode='between' onModeChange={onModeChange}/>
            </WindowTitlebar>
        ) : (
            <InnerBar disabled={disabled} mode={mode} justify_mode='center' onModeChange={onModeChange}/>
        )
    );
}
