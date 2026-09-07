import type {ReactNode} from 'react';
import * as TooltipPrimitive from '@radix-ui/react-tooltip';

export function Tooltip({label, children}: {label: string; children: ReactNode}) {
    return (
        <TooltipPrimitive.Provider delayDuration={300}>
            <TooltipPrimitive.Root>
                <TooltipPrimitive.Trigger asChild>
                    <span className="inline-flex">{children}</span>
                </TooltipPrimitive.Trigger>
                <TooltipPrimitive.Portal>
                    <TooltipPrimitive.Content side="bottom" sideOffset={6} collisionPadding={8}
                        className="z-[200] max-w-64 rounded-md border px-2.5 py-1.5 text-xs shadow-lg"
                        style={{backgroundColor: 'var(--app-bg-secondary)', borderColor: 'var(--app-border)', color: 'var(--app-text-primary)'}}>
                        {label}
                    </TooltipPrimitive.Content>
                </TooltipPrimitive.Portal>
            </TooltipPrimitive.Root>
        </TooltipPrimitive.Provider>
    );
}
