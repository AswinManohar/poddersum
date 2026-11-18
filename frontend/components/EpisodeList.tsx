import Link from "next/link";
import type { EpisodeRef } from "@/lib/models";
import { formatDate, formatDuration } from "@/lib/format";
import { StatusBadge, TranscriptBadge } from "./Badge";

export function EpisodeMeta({ publishedAt, durationS }: { publishedAt: string | null; durationS: number | null }) {
  const date = formatDate(publishedAt);
  const duration = formatDuration(durationS);
  if (!date && !duration) return null;
  return (
    <span className="text-sm text-ink-muted">
      {date ? <time dateTime={publishedAt ?? undefined}>{date}</time> : null}
      {date && duration ? <span aria-hidden="true"> &middot; </span> : null}
      {duration ? <span>{duration}</span> : null}
    </span>
  );
}

export function EpisodeList({ episodes }: { episodes: EpisodeRef[] }) {
  return (
    <ul className="divide-y divide-line overflow-hidden rounded-xl border border-line bg-surface">
      {episodes.map((ep) => (
        <li key={ep.id}>
          <Link
            href={`/episode/${ep.id}`}
            className="block px-4 py-4 transition-colors hover:bg-surface-muted"
          >
            <span className="block font-medium text-ink">{ep.title}</span>
            <span className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1">
              <EpisodeMeta publishedAt={ep.published_at} durationS={ep.duration_s} />
              {ep.has_feed_transcript ? <TranscriptBadge /> : null}
              <StatusBadge status={ep.status} />
            </span>
          </Link>
        </li>
      ))}
    </ul>
  );
}
