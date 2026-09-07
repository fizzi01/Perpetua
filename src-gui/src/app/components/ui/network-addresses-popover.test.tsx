import {cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {afterEach, describe, expect, it, vi} from 'vitest';
import {NetworkAddressesPopover} from './network-addresses-popover';

afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
});

const entry = {address: '192.168.1.20', interfaceName: 'Wi-Fi', copyValue: '192.168.1.20:5555'};
function open() {
    const trigger = screen.getByRole('button', {name: 'Network addresses · 1'});
    trigger.focus();
    fireEvent.click(trigger);
    return trigger;
}

describe('NetworkAddressesPopover', () => {
    it('hides an empty list', () => {
        render(<NetworkAddressesPopover title="Network addresses" entries={[]}/>);
        expect(screen.queryByRole('button')).not.toBeInTheDocument();
    });

    it('deduplicates addresses and closes with Escape, restoring focus', async () => {
        const {container} = render(<NetworkAddressesPopover title="Network addresses" entries={[entry, entry]}/>);
        const compactTrigger = screen.getByRole('button', {name: 'Network addresses · 1'});
        expect(compactTrigger).toHaveTextContent(/^1$/);
        expect(screen.queryByText('Network addresses · 1')).not.toBeInTheDocument();
        const trigger = open();
        expect(screen.getByText('Network addresses · 1')).toBeInTheDocument();
        expect(screen.getByText(entry.copyValue)).toBeInTheDocument();
        expect(container.querySelector('[role="dialog"]')).toBeNull();
        fireEvent.keyDown(screen.getByRole('dialog'), {key: 'Escape'});
        await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
        await waitFor(() => expect(trigger).toHaveFocus());
    });

    it('reports success only after clipboard completion', async () => {
        let finish!: () => void;
        const writeText = vi.fn(() => new Promise<void>(resolve => { finish = resolve; }));
        Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {writeText}});
        render(<NetworkAddressesPopover title="Network addresses" entries={[entry]}/>);
        open();
        fireEvent.click(screen.getByRole('button', {name: `Copy ${entry.copyValue}`}));
        expect(writeText).toHaveBeenCalledWith(entry.copyValue);
        expect(screen.queryByText('Copied')).not.toBeInTheDocument();
        finish();
        expect(await screen.findByText('Copied')).toBeInTheDocument();
    });

    it('reports copy failure and supports unnamed addresses', async () => {
        Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {
            writeText: vi.fn().mockRejectedValue(new Error('denied')),
        }});
        render(<NetworkAddressesPopover title="Network addresses" entries={[{address: '::1', copyValue: '::1'}]}/>);
        open();
        fireEvent.click(screen.getByRole('button', {name: 'Copy ::1'}));
        expect(await screen.findByText(/Could not copy/)).toBeInTheDocument();
        expect(screen.queryByText('Copied')).not.toBeInTheDocument();
    });

    it('keeps many full addresses inside the scrollable list', () => {
        const entries = Array.from({length: 20}, (_, i) => ({
            address: `2001:db8:1234:5678:abcd:ef01:2345:${i}`,
            copyValue: `2001:db8:1234:5678:abcd:ef01:2345:${i}`,
            interfaceName: `A long interface name ${i}`,
        }));
        render(<NetworkAddressesPopover title="Network addresses" entries={entries}/>);
        fireEvent.click(screen.getByRole('button', {name: 'Network addresses · 20'}));
        expect(screen.getAllByRole('listitem')).toHaveLength(20);
        expect(screen.getByText(entries[19].address)).toBeInTheDocument();
        expect(screen.getByRole('list')).toHaveClass('overflow-y-auto');
    });
});
