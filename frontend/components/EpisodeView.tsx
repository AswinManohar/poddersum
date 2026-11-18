import Link from "next/link";
import { ApiError, getEpisode } from "@/lib/api";
import { Artwork } from "./Artwork";
import { StatusBadge, TranscriptBadge } from "./Badge";
import { EpisodeMeta } from "./EpisodeList";
import { EpisodeWorkspace } from "./EpisodeWorkspace";
import { Notice } from "./Notice";

/** Server component: episode metadata, the original-audio link and the workspace. */
export async function EpisodeView({ episodeId }: { episodeId: string }) {
  let episode;
  try {
    episode = await getEpisode(episodeId);
  } catch (err) {
    if (err instanceof ApiError && (err.status === 404 || err.status === 422)) {
      return (
        <Notice title="We don't know this episode yet" action={{ href: "/", label: "Search for its show" }}>
          <p>
            PodderSum learns about episodes when their show is opened. Search for the show, open
            it, then pick the episode.
          </p>
        </Notice>
      );
    }
    const message = err instanceof ApiError ? err.detail : "Something went wrong.";
    return (
      <Notice title="Could not load this episode" tone="error" action={{ href: "/", label: "Back to search" }}>
        <p>{message}</p>
      </Notice>
    );
  }

  return (
    <article className="space-y-8">
      <header className="space-y-4">
        <Link
          href={`/podcast/${episode.podcast.id}`}
          className="inline-flex items-center gap-1 text-sm font-medium text-accent underline-offset-4 hover:underline"
        >
          <span aria-hidden="true">&larr;</span> {episode.podcast.title}
        </Link>
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start">
          <Artwork src={episode.artwork_url ?? episode.podcast.artwork_url} alt="" size={112} />
          <div className="min-w-0 space-y-2">
            <h1 className="font-display text-2xl font-semibold tracking-tight break-words sm:text-3xl">
              {episode.title}
            </h1>
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
              <EpisodeMeta publishedAt={episode.published_at} durationS={episode.duration_s} />
              {episode.has_feed_transcript ? <TranscriptBadge /> : null}
              <StatusBadge status={episode.status} />
            </div>
          </div>
        </div>
        <a
          href={episode.audio_url}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-2 rounded-lg border border-line bg-surface px-4 py-2 font-medium text-ink hover:border-accent"
        >
          Open original audio
          <span className="sr-only">(opens in a new tab)</span>
          <span aria-hidden="true">&#8599;</span>
        </a>
      </header>

      <EpisodeWorkspace
        episodeId={episode.id}
        status={episode.status}
        activeJobId={episode.active_job_id}
        audioUrl={episode.audio_url}
      />

      {episode.description ? (
        <section aria-labelledby="notes-heading" className="space-y-2">
          <h2 id="notes-heading" className="font-display text-xl font-semibold">
            Show notes
          </h2>
          <p className="whitespace-pre-line break-words text-ink-muted">{episode.description}</p>
        </section>
      ) : null}
    </article>
  );
}
