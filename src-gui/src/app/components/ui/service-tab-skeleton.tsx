interface ServiceTabSkeletonProps {
    mode: 'client' | 'server';
}

/** Mirrors the service header, summary cards and permissions panel. */
export function ServiceTabSkeleton({mode}: ServiceTabSkeletonProps) {
    const bar = 'rounded-md motion-safe:animate-pulse bg-[var(--app-border)]';
    const cardStyle = {backgroundColor: 'var(--app-card-bg)', borderColor: 'var(--app-card-border)'};
    return (
        <div role="status" aria-label="Loading configuration" aria-busy="true" className="space-y-6">
            <span className="sr-only">Loading configuration…</span>
            <div aria-hidden="true" className="space-y-6">
                <div className="flex items-center justify-between gap-4">
                    <div className="space-y-3">
                        <div className={`${bar} h-7 w-36`}/>
                        <div className={`${bar} h-3 w-48`}/>
                    </div>
                    <div className={`${bar} h-14 w-14 !rounded-full`}/>
                </div>
                <div className={mode === 'server' ? 'grid grid-cols-2 gap-4' : 'grid grid-cols-1 gap-4'}>
                    {Array.from({length: mode === 'server' ? 2 : 1}, (_, index) => (
                        <div key={index} className="flex items-center gap-3 p-4 rounded-lg border" style={cardStyle}>
                            <div className={`${bar} h-10 w-10 shrink-0`}/>
                            <div className="space-y-2 flex-1">
                                <div className={`${bar} h-4 w-28`}/>
                                <div className={`${bar} h-3 w-20`}/>
                            </div>
                        </div>
                    ))}
                </div>
                <div className="p-4 rounded-lg border space-y-5" style={cardStyle}>
                    <div className={`${bar} h-5 w-32`}/>
                    {[0, 1, 2].map(index => (
                        <div key={index} className="flex items-center justify-between gap-4">
                            <div className="space-y-2">
                                <div className={`${bar} h-4 w-28`}/>
                                <div className={`${bar} h-3 w-44`}/>
                            </div>
                            <div className={`${bar} h-6 w-10 !rounded-full`}/>
                        </div>
                    ))}
                </div>
                <div className="flex gap-3">
                    <div className={`${bar} h-9 w-28`}/>
                    <div className={`${bar} h-9 w-28`}/>
                </div>
            </div>
        </div>
    );
}
