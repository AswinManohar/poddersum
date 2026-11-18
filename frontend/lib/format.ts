/** Pure display helpers. Keep them deterministic (explicit locale and time zone) so server
 * and client render the same text. */

/** 3725 -> "1 h 2 min", 754 -> "12 min", 42 -> "1 min", null -> null. */
export function formatDuration(seconds: number | null | undefined): string | null {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds) || seconds <= 0) {
    return null;
  }
  const totalMinutes = Math.max(1, Math.round(seconds / 60));
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  if (hours === 0) return `${minutes} min`;
  if (minutes === 0) return `${hours} h`;
  return `${hours} h ${minutes} min`;
}

/** Milliseconds -> "m:ss" or "h:mm:ss" (citation timestamps). */
export function formatTimestamp(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const ss = String(s).padStart(2, "0");
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}

const DATE_FORMAT = new Intl.DateTimeFormat("en-GB", {
  day: "numeric",
  month: "short",
  year: "numeric",
  timeZone: "UTC",
});

/** ISO-8601 -> "3 Nov 2026" (UTC). Invalid or missing -> null. */
export function formatDate(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  return DATE_FORMAT.format(date);
}

/**
 * Link to the original audio at a moment. Uses a Media Fragment (`#t=<seconds>`), which
 * browsers honour when they open an audio URL directly. Seconds are rounded down; any
 * existing fragment is replaced and the query string is kept as is.
 */
export function audioLinkAt(audioUrl: string, startMs: number): string {
  const seconds = Number.isFinite(startMs) ? Math.max(0, Math.floor(startMs / 1000)) : 0;
  const hash = audioUrl.indexOf("#");
  const base = hash === -1 ? audioUrl : audioUrl.slice(0, hash);
  return `${base}#t=${seconds}`;
}

/** Model cost in US dollars: 0.00038 -> "$0.0004", 0.0123 -> "$0.0123", 1.5 -> "$1.50". */
export function formatCost(usd: number): string {
  if (!Number.isFinite(usd) || usd <= 0) return "$0.00";
  if (usd < 0.0001) return "<$0.0001";
  if (usd < 1) return `$${usd.toFixed(4)}`;
  return `$${usd.toFixed(2)}`;
}

const STEP_LABELS: Record<string, string> = {
  queued: "Waiting to start",
  fetch_transcript: "Fetching the transcript",
  download_audio: "Downloading the audio",
  transcribe: "Transcribing the audio",
  segment: "Splitting into timestamped segments",
  store: "Saving the transcript",
  done: "Done",
};

/** Ingest step id -> plain words. Unknown steps are de-snaked rather than hidden. */
export function stepLabel(step: string): string {
  return STEP_LABELS[step] ?? step.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}
