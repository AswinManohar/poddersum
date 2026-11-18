"use client";

import { forwardRef, useId, type KeyboardEvent } from "react";

interface ComposerProps {
  label: string;
  hint?: string;
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  busy: boolean;
  submitLabel: string;
  onCancel?: () => void;
  placeholder?: string;
  rows?: number;
}

export const MAX_QUESTION_LENGTH = 2000;

/** Textarea plus submit. Enter submits, Shift+Enter adds a new line. */
export const Composer = forwardRef<HTMLTextAreaElement, ComposerProps>(function Composer(
  { label, hint, value, onChange, onSubmit, busy, submitLabel, onCancel, placeholder, rows = 3 },
  ref,
) {
  const id = useId();
  const hintId = useId();
  const empty = value.trim() === "";

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key !== "Enter" || e.shiftKey || e.nativeEvent.isComposing) return;
    e.preventDefault();
    if (!busy && !empty) onSubmit();
  }

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (!busy && !empty) onSubmit();
      }}
      className="space-y-2"
    >
      <label htmlFor={id} className="block font-medium">
        {label}
      </label>
      <textarea
        ref={ref}
        id={id}
        value={value}
        rows={rows}
        maxLength={MAX_QUESTION_LENGTH}
        placeholder={placeholder}
        aria-describedby={hintId}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={onKeyDown}
        className="block w-full resize-y rounded-lg border border-line bg-bg px-3 py-2 text-ink placeholder:text-ink-muted/70 focus:border-accent"
      />
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p id={hintId} className="text-xs text-ink-muted">
          {hint ?? "Enter to send, Shift+Enter for a new line."}
        </p>
        <div className="flex gap-2">
          {busy && onCancel ? (
            <button
              type="button"
              onClick={onCancel}
              className="rounded-lg border border-line bg-surface px-4 py-2 font-medium text-ink hover:border-accent"
            >
              Stop
            </button>
          ) : null}
          <button
            type="submit"
            disabled={busy || empty}
            className="rounded-lg bg-accent px-4 py-2 font-medium text-accent-ink disabled:cursor-not-allowed disabled:opacity-50"
          >
            {busy ? "Working..." : submitLabel}
          </button>
        </div>
      </div>
    </form>
  );
});
