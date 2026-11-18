export function ListSkeleton({ rows = 5, label }: { rows?: number; label: string }) {
  return (
    <div role="status" aria-live="polite" className="space-y-3">
      <span className="sr-only">{label}</span>
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="flex animate-pulse items-center gap-4 rounded-xl border border-line bg-surface p-4">
          <div className="h-14 w-14 shrink-0 rounded-lg bg-surface-muted" />
          <div className="flex-1 space-y-2">
            <div className="h-4 w-2/3 rounded bg-surface-muted" />
            <div className="h-3 w-1/3 rounded bg-surface-muted" />
          </div>
        </div>
      ))}
    </div>
  );
}
