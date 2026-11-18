import { stepLabel } from "@/lib/format";
import type { JobEvent, JobStatus } from "@/lib/models";
import type { Problem } from "@/lib/errors";
import { ProblemState } from "./ProblemState";

export type JobSnapshot = Pick<JobEvent | JobStatus, "state" | "step" | "progress" | "message">;

/** Live ingest progress: plain-words step, a real progressbar, and the latest message. */
export function JobProgress({ job }: { job: JobSnapshot | null }) {
  const pct = job ? Math.round(Math.min(1, Math.max(0, job.progress)) * 100) : 0;
  const label = job ? stepLabel(job.step) : "Starting";
  return (
    <div className="space-y-3" data-testid="job-progress">
      <div aria-live="polite" className="space-y-1">
        <p className="font-medium">
          {label}
          <span className="sr-only">, {pct}% done</span>
        </p>
        {job?.message ? <p className="text-sm text-ink-muted">{job.message}</p> : null}
      </div>
      <div
        role="progressbar"
        aria-label="Processing progress"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct}
        aria-valuetext={`${pct}%, ${label}`}
        className="h-2 w-full overflow-hidden rounded-full bg-surface-muted"
      >
        <div className="h-full rounded-full bg-accent transition-[width] duration-500" style={{ width: `${pct}%` }} />
      </div>
      <p className="text-xs text-ink-muted">
        You can leave or refresh this page; processing continues and picks up where it is.
      </p>
    </div>
  );
}

export function JobFailed({ message, onRetry }: { message: string | null; onRetry: () => void }) {
  return (
    <div role="alert" className="rounded-lg border border-danger/40 bg-danger-soft p-4" data-testid="job-failed">
      <p className="font-semibold">Processing this episode failed</p>
      <p className="mt-1 break-words text-sm text-ink-muted">
        {message ?? "The last attempt to prepare this episode's transcript did not finish."}
      </p>
      <button
        type="button"
        onClick={onRetry}
        className="mt-3 rounded-lg bg-accent px-4 py-2 font-medium text-accent-ink"
      >
        Try again
      </button>
    </div>
  );
}

export function JobLost({ problem, onRetry }: { problem: Problem; onRetry: () => void }) {
  return <ProblemState problem={problem} onRetry={onRetry} retryLabel="Reconnect" />;
}
