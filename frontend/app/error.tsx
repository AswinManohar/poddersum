"use client";

export default function ErrorPage({ retry }: { error: Error & { digest?: string }; retry: () => void }) {
  return (
    <div role="alert" className="rounded-xl border border-danger/40 bg-danger-soft p-6">
      <h2 className="font-display text-lg font-semibold">Something went wrong</h2>
      <p className="mt-2 text-ink-muted">The page failed to load. Try again in a moment.</p>
      <button
        type="button"
        onClick={() => retry()}
        className="mt-4 font-medium text-accent underline underline-offset-4"
      >
        Try again
      </button>
    </div>
  );
}
