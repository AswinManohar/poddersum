import type { PodcastHit } from "@/lib/models";
import { Artwork } from "./Artwork";

export function PodcastHeader({ podcast }: { podcast: PodcastHit }) {
  return (
    <div className="flex flex-col gap-4 sm:flex-row sm:items-end">
      <Artwork src={podcast.artwork_url} alt={`${podcast.title} artwork`} size={128} />
      <div className="min-w-0">
        {podcast.genre ? (
          <p className="text-xs uppercase tracking-wide text-ink-muted">{podcast.genre}</p>
        ) : null}
        <h1 className="font-display text-3xl font-semibold tracking-tight break-words">
          {podcast.title}
        </h1>
        {podcast.author ? <p className="mt-1 text-ink-muted">{podcast.author}</p> : null}
      </div>
    </div>
  );
}
