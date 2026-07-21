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
import {ShieldAlert, ExternalLink} from 'lucide-react';
import {PermissionInfo} from '../../api/Interface';
import {requestPermissions} from '../../api/Sender';

interface PermissionGateProps {
    /** The OS-level permissions currently missing (e.g. macOS Accessibility). */
    missing: PermissionInfo[];
    /** Service that will auto-start once the permission is granted, if any. */
    pendingService?: string | null;
    /** True when a permission was revoked while the app was already running. */
    revoked?: boolean;
}

/** Turn a snake_case permission type into a human-readable label. */
function prettyName(type: string): string {
    return type
        .split('_')
        .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
        .join(' ');
}

/**
 * Full-screen overlay shown when the daemon reports missing OS-level
 * permissions (macOS Accessibility / Input Monitoring). The daemon has already
 * bound its command socket, so the GUI stays connected; this gate guides the
 * user through granting the permission and dismisses itself automatically when
 * the daemon reports the grant (``permissions_granted``).
 */
export function PermissionGate({missing, pendingService, revoked}: PermissionGateProps) {
    const handleGrant = () => {
        // No argument -> the daemon requests every missing permission and opens
        // the relevant System Settings pane(s).
        requestPermissions().catch((err) => {
            console.error('[PermissionGate] Failed to request permissions:', err);
        });
    };

    return (
        <motion.div
            initial={{opacity: 0}}
            animate={{opacity: 1}}
            className="fixed inset-0 z-50 flex items-center justify-center p-8 backdrop-blur-sm"
            style={{backgroundColor: 'var(--app-bg)'}}
        >
            <motion.div
                initial={{opacity: 0, scale: 0.96, y: 8}}
                animate={{opacity: 1, scale: 1, y: 0}}
                transition={{duration: 0.25}}
                className="w-full max-w-md rounded-xl border p-6 shadow-lg"
                style={{
                    backgroundColor: 'var(--app-card-bg)',
                    borderColor: 'var(--app-card-border)',
                }}
            >
                <div className="flex items-center gap-3 mb-3">
                    <ShieldAlert size={28} style={{color: 'var(--app-danger)'}}/>
                    <h2 className="text-lg font-semibold" style={{color: 'var(--app-text-primary)'}}>
                        {revoked ? 'Permission revoked' : 'Permission required'}
                    </h2>
                </div>

                <p className="text-sm mb-4" style={{color: 'var(--app-text-secondary)'}}>
                    {revoked
                        ? 'A required system permission was turned off while Perpetua was running, so the service was stopped to avoid locking your input. Re-enable it in System Settings and the service will resume automatically.'
                        : 'Perpetua needs the following system permission(s) to control your keyboard and mouse. Grant them in System Settings, then this screen will continue automatically.'}
                </p>

                <ul className="flex flex-col gap-2 mb-5">
                    {missing.map((perm) => (
                        <li
                            key={perm.type}
                            className="flex flex-col rounded-lg border px-3 py-2"
                            style={{
                                backgroundColor: 'var(--app-danger-bg)',
                                borderColor: 'var(--app-card-border)',
                            }}
                        >
                            <span className="text-sm font-semibold" style={{color: 'var(--app-text-primary)'}}>
                                {prettyName(perm.type)}
                            </span>
                            {perm.message ? (
                                <span className="text-xs" style={{color: 'var(--app-text-secondary)'}}>
                                    {perm.message}
                                </span>
                            ) : null}
                        </li>
                    ))}
                </ul>

                <motion.button
                    whileHover={{scale: 1.02}}
                    whileTap={{scale: 0.98}}
                    onClick={handleGrant}
                    className="w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg font-semibold transition-all"
                    style={{
                        backgroundColor: 'var(--app-accent)',
                        color: 'var(--app-accent-contrast, #fff)',
                    }}
                >
                    <ExternalLink size={16}/>
                    Open Settings
                </motion.button>

                <p className="text-xs mt-4 text-center" style={{color: 'var(--app-text-secondary)'}}>
                    {pendingService
                        ? `The ${pendingService} service will start automatically once granted.`
                        : 'Waiting for permission to be granted…'}
                    {' '}If it doesn't continue after granting, try restarting the app.
                </p>
            </motion.div>
        </motion.div>
    );
}
