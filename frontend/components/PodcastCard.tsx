import Link from "next/link";
import type { PodcastHit } from "@/lib/models";
import { Artwork } from "./Artwork";

export function PodcastCard({ podcast }: { podcast: PodcastHit }) {
  return (
    <Link
      href={`/podcast/${podcast.id}`}
      className="flex items-center gap-4 rounded-xl border border-line bg-surface p-3 transition-colors hover:border-accent sm:p-4"
    >
      <Artwork src={podcast.artwork_url} alt="" size={64} />
      <span className="min-w-0 flex-1">
        <span className="block truncate font-semibold text-ink">{podcast.title}</span>
        {podcast.author ? (
          <span className="block truncate text-sm text-ink-muted">{podcast.author}</span>
        ) : null}
        {podcast.genre ? (
          <span className="mt-1 block text-xs uppercase tracking-wide text-ink-muted">
            {podcast.genre}
          </span>
        ) : null}
      </span>
    </Link>
  );
}
