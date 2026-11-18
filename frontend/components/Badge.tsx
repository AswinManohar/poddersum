import type { EpisodeStatus } from "@/lib/models";

type Tone = "neutral" | "ok" | "warn" | "danger" | "accent";

const TONES: Record<Tone, string> = {
  neutral: "bg-surface-muted text-ink-muted",
  ok: "bg-ok-soft text-ok",
  warn: "bg-warn-soft text-warn",
  danger: "bg-danger-soft text-danger",
  accent: "bg-accent-soft text-accent",
};

export function Badge({ tone = "neutral", children }: { tone?: Tone; children: React.ReactNode }) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${TONES[tone]}`}
    >
      {children}
    </span>
  );
}

const STATUS: Record<EpisodeStatus, { label: string; tone: Tone }> = {
  new: { label: "New", tone: "neutral" },
  processing: { label: "Processing", tone: "warn" },
  ready: { label: "Ready", tone: "ok" },
  failed: { label: "Failed", tone: "danger" },
};

/** Status badge; renders nothing for `new` (the default, unprocessed state). */
export function StatusBadge({ status }: { status: EpisodeStatus }) {
  if (status === "new") return null;
  const { label, tone } = STATUS[status];
  return <Badge tone={tone}>{label}</Badge>;
}

export function TranscriptBadge() {
  return <Badge tone="accent">Transcript in feed</Badge>;
}
