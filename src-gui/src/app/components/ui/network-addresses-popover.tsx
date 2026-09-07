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

import {useEffect, useId, useRef, useState} from 'react';
import * as Popover from '@radix-ui/react-popover';
import {Check, Copy, Network, X} from 'lucide-react';

export interface NetworkAddressEntry {
    address: string;
    interfaceName?: string;
    copyValue: string;
}

interface NetworkAddressesPopoverProps {
    title: string;
    entries: NetworkAddressEntry[];
}

function AddressRow({entry}: {entry: NetworkAddressEntry}) {
    const [status, setStatus] = useState<'idle' | 'copied' | 'error'>('idle');
    const resetRef = useRef<ReturnType<typeof setTimeout>>();
    const mounted = useRef(true);
    useEffect(() => {
        mounted.current = true;
        return () => {
            mounted.current = false;
            clearTimeout(resetRef.current);
        };
    }, []);

    const copy = async () => {
        clearTimeout(resetRef.current);
        setStatus('idle');
        try {
            await navigator.clipboard.writeText(entry.copyValue);
            if (!mounted.current) return;
            setStatus('copied');
            resetRef.current = setTimeout(() => setStatus('idle'), 1500);
        } catch {
            if (mounted.current) setStatus('error');
        }
    };

    return (
        <li className="px-3 py-2.5">
            <div className="flex items-center gap-3">
                <div className="flex-1 min-w-0 select-text">
                    {entry.interfaceName && (
                        <div className="text-xs mb-1 break-words" style={{color: 'var(--app-text-muted)'}}>
                            {entry.interfaceName}
                        </div>
                    )}
                    <div className="text-xs font-mono break-all" style={{color: 'var(--app-text-primary)'}}>
                        {entry.copyValue}
                    </div>
                </div>
                <button type="button" onClick={copy} aria-label={`Copy ${entry.copyValue}`}
                        title={`Copy ${entry.copyValue}`}
                        className="p-2 rounded-md shrink-0 cursor-pointer hover:opacity-80 focus-visible:outline-2 focus-visible:outline-offset-2"
                        style={{backgroundColor: 'var(--app-bg-tertiary)', color: status === 'copied' ? 'var(--app-success)' : 'var(--app-text-muted)'}}>
                    {status === 'copied' ? <Check size={14}/> : <Copy size={14}/>}
                </button>
            </div>
            <div role="status" aria-live="polite" className="text-xs" style={{color: status === 'error' ? 'var(--app-warning)' : 'var(--app-success)'}}>
                {status === 'copied' && 'Copied'}
                {status === 'error' && 'Could not copy. Select the address and copy it manually.'}
            </div>
        </li>
    );
}

export function NetworkAddressesPopover({title, entries}: NetworkAddressesPopoverProps) {
    const titleId = useId();
    const seen = new Set<string>();
    const addresses = entries.filter(entry => {
        if (seen.has(entry.address)) return false;
        seen.add(entry.address);
        return true;
    });
    if (!addresses.length) return null;

    return (
        <Popover.Root>
            <Popover.Trigger asChild>
                <button type="button" aria-label={`${title} · ${addresses.length}`}
                        className="inline-flex max-w-full shrink-0 items-center gap-1.5 rounded-md border px-2 py-1.5 text-xs cursor-pointer hover:opacity-80 focus-visible:outline-2 focus-visible:outline-offset-2"
                        style={{borderColor: 'var(--app-border)', color: 'var(--app-text-muted)', backgroundColor: 'var(--app-bg-tertiary)'}}>
                    <Network size={14} className="shrink-0"/>
                    <span className="tabular-nums">{addresses.length}</span>
                </button>
            </Popover.Trigger>
            <Popover.Portal>
                <Popover.Content align="end" sideOffset={8} collisionPadding={12} aria-labelledby={titleId}
                                 className="z-[100] rounded-lg border shadow-xl overflow-hidden"
                                 style={{width: 320, maxWidth: 'calc(100vw - 24px)', maxHeight: 'var(--radix-popover-content-available-height)', backgroundColor: 'var(--app-bg-secondary)', borderColor: 'var(--app-border)', color: 'var(--app-text-primary)'}}>
                    <div className="flex items-center justify-between gap-2 px-3 py-2 border-b" style={{borderColor: 'var(--app-border)'}}>
                        <h4 id={titleId} className="text-xs font-semibold">{title} · {addresses.length}</h4>
                        <Popover.Close aria-label="Close network addresses" className="p-1 rounded cursor-pointer hover:opacity-80">
                            <X size={14}/>
                        </Popover.Close>
                    </div>
                    <ul className="overflow-y-auto overscroll-contain" style={{maxHeight: 'min(280px, calc(var(--radix-popover-content-available-height) - 44px))'}}>
                        {addresses.map(entry => <AddressRow key={entry.address} entry={entry}/>)}
                    </ul>
                </Popover.Content>
            </Popover.Portal>
        </Popover.Root>
    );
}
