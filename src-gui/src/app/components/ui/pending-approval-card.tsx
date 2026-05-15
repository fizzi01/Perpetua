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

import {useEffect, useState} from 'react';
import {motion} from 'motion/react';
import {Check, UserPlus, X} from 'lucide-react';
import {ClientApprovalRequest} from '../../api/Interface';

type ScreenPosition = 'top' | 'bottom' | 'left' | 'right';

const POSITIONS: ScreenPosition[] = ['top', 'bottom', 'left', 'right'];

interface PendingApprovalCardProps {
    request: ClientApprovalRequest;
    onApprove: (position: ScreenPosition) => void;
    onDeny: () => void;
}

/**
 * Inline allow/deny prompt for an unknown client trying to connect. The
 * handshake on the server is held open until the admin makes a choice, so the
 * card surfaces a countdown derived from the timeout the server reported.
 */
export function PendingApprovalCard({request, onApprove, onDeny}: PendingApprovalCardProps) {
    const [position, setPosition] = useState<ScreenPosition>('top');
    const [remaining, setRemaining] = useState(request.timeout);

    useEffect(() => {
        if (!request.timeout || request.timeout <= 0) return;
        const startedAt = Date.now();
        const id = setInterval(() => {
            const elapsed = Math.floor((Date.now() - startedAt) / 1000);
            setRemaining(Math.max(0, request.timeout - elapsed));
        }, 1000);
        return () => clearInterval(id);
    }, [request.timeout]);

    const displayName = request.hostname || request.peer_ip;

    return (
        <motion.div
            initial={{opacity: 0, y: -20, scale: 0.95}}
            animate={{opacity: 1, y: 0, scale: 1}}
            exit={{opacity: 0, y: -20, scale: 0.95}}
            transition={{duration: 0.3, ease: 'easeOut'}}
            className="p-5 rounded-xl border-2 backdrop-blur-sm"
            style={{
                backgroundColor: 'var(--app-card-bg)',
                borderColor: 'var(--app-primary)',
                boxShadow: '0 8px 32px rgba(0, 0, 0, 0.12)',
            }}
        >
            <div className="flex items-center gap-3 mb-4">
                <motion.div
                    animate={{scale: [1, 1.08, 1]}}
                    transition={{duration: 2, repeat: Infinity, ease: 'easeInOut'}}
                    className="w-12 h-12 rounded-lg flex items-center justify-center"
                    style={{backgroundColor: 'var(--app-primary)'}}
                >
                    <UserPlus size={24} style={{color: 'white'}}/>
                </motion.div>
                <div className="flex-1 min-w-0">
                    <h3 className="text-base font-bold truncate"
                        style={{color: 'var(--app-text-primary)'}}
                    >
                        {displayName}
                    </h3>
                    <p className="text-xs mt-1 truncate"
                       style={{color: 'var(--app-text-muted)'}}
                    >
                        {request.hostname
                            ? `${request.peer_ip} wants to connect`
                            : 'wants to connect'}
                        {remaining > 0 && ` · ${remaining}s left`}
                    </p>
                </div>
            </div>

            <div className="mb-4">
                <label className="block mb-2 text-sm font-semibold"
                       style={{color: 'var(--app-text-primary)'}}
                >
                    Screen position
                </label>
                <div className="grid grid-cols-4 gap-1">
                    {POSITIONS.map((p) => (
                        <motion.button
                            key={p}
                            whileHover={{scale: 1.05}}
                            whileTap={{scale: 0.95}}
                            onClick={() => setPosition(p)}
                            className="cursor-pointer px-2 py-2 rounded-lg text-xs font-semibold transition-all capitalize"
                            style={{
                                backgroundColor:
                                    position === p
                                        ? 'var(--app-primary)'
                                        : 'var(--app-bg-secondary)',
                                color:
                                    position === p
                                        ? 'white'
                                        : 'var(--app-text-secondary)',
                                border:
                                    position === p
                                        ? '1px solid var(--app-primary)'
                                        : '1px solid var(--app-input-border)',
                            }}
                        >
                            {p}
                        </motion.button>
                    ))}
                </div>
            </div>

            <div className="grid grid-cols-2 gap-2">
                <motion.button
                    whileHover={{scale: 1.02}}
                    whileTap={{scale: 0.98}}
                    onClick={onDeny}
                    className="cursor-pointer p-3 rounded-lg transition-all flex items-center justify-center gap-2"
                    style={{
                        backgroundColor: 'var(--app-danger)',
                        color: 'white',
                    }}
                >
                    <X size={18}/>
                    <span className="text-sm font-medium">Deny</span>
                </motion.button>
                <motion.button
                    whileHover={{scale: 1.02}}
                    whileTap={{scale: 0.98}}
                    onClick={() => onApprove(position)}
                    className="cursor-pointer p-3 rounded-lg transition-all flex items-center justify-center gap-2"
                    style={{
                        backgroundColor: 'var(--app-success)',
                        color: 'white',
                    }}
                >
                    <Check size={18}/>
                    <span className="text-sm font-medium">Allow</span>
                </motion.button>
            </div>
        </motion.div>
    );
}
