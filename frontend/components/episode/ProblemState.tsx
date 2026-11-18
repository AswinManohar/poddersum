import type { Problem } from "@/lib/errors";

interface ProblemStateProps {
  problem: Problem;
  onRetry?: () => void;
  retryLabel?: string;
  compact?: boolean;
}

/** Error state of a panel: plain-words title and advice, the server's text, a retry. */
export function ProblemState({ problem, onRetry, retryLabel = "Try again", compact = false }: ProblemStateProps) {
  const config = problem.kind === "model_auth";
  const quiet = problem.kind === "not_available";
  const tone = config
    ? "border-warn/40 bg-warn-soft"
    : quiet
      ? "border-line bg-surface-muted"
      : "border-danger/40 bg-danger-soft";
  return (
    <div
      role={quiet ? "status" : "alert"}
      data-problem={problem.kind}
      className={`rounded-lg border ${tone} ${compact ? "p-3" : "p-4"}`}
    >
      <p className="font-semibold text-ink">{problem.title}</p>
      <p className="mt-1 text-sm text-ink-muted">{problem.message}</p>
      {problem.detail && problem.detail !== problem.message ? (
        <p className="mt-2 break-words font-mono text-xs text-ink-muted">
          <span className="sr-only">Server message: </span>
          {problem.detail}
        </p>
      ) : null}
      {onRetry && problem.retryable ? (
        <button
          type="button"
          onClick={onRetry}
          className="mt-3 rounded-lg border border-line bg-surface px-3 py-1.5 text-sm font-medium text-ink hover:border-accent"
        >
          {retryLabel}
        </button>
      ) : null}
    </div>
  );
}

/** Text-shaped loading placeholder for answer panels. */
export function TextSkeleton({ lines = 3, label }: { lines?: number; label: string }) {
  const widths = ["w-11/12", "w-4/5", "w-2/3", "w-3/4", "w-1/2"];
  return (
    <div role="status" className="space-y-3">
      <span className="sr-only">{label}</span>
      {Array.from({ length: lines }, (_, i) => (
        <div key={i} className="animate-pulse space-y-2">
          <div className={`h-4 rounded bg-surface-muted ${widths[i % widths.length]}`} />
          <div className="h-3 w-16 rounded bg-surface-muted" />
        </div>
      ))}
    </div>
  );
}
