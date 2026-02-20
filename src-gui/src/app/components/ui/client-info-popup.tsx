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

import {AnimatePresence, motion} from 'motion/react';

interface ClientInfoPopupProps {
    uid?: string;
    show: boolean;
    clientRect?: DOMRect;
    onMouseEnter?: () => void;
    onMouseLeave?: () => void;
}

// {/* Client Info Popup - Rendered at top level */}
// <ClientInfoPopup 
//   uid={clientManager.clients.find(c => c.id === hoveredClientId)?.uid}
//   show={showPopup && hoveredClientId !== null}
//   clientRect={clientRect || undefined}
//   onMouseEnter={handlePopupMouseEnter}
//   onMouseLeave={handlePopupMouseLeave}
// />

export function ClientInfoPopup({uid, show, clientRect, onMouseEnter, onMouseLeave}: ClientInfoPopupProps) {
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
                    initial={{opacity: 0, y: 10}}
                    animate={{opacity: 1, y: 0}}
                    exit={{opacity: 0, y: 10}}
                    transition={{duration: 0.2}}
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
                        <p className="text-sm font-semibold mb-2 select-none"
                           style={{color: 'var(--app-primary)'}}>Client UID</p>
                        <p className="text-xs font-mono break-all max-w-xs select-text cursor-text"
                           style={{color: 'var(--app-text-muted)'}}>{uid}</p>
                    </div>
                </motion.div>
            )}
        </AnimatePresence>
    );
}
