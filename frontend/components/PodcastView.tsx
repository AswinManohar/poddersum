import { ApiError, getPodcastEpisodes } from "@/lib/api";
import { EpisodeList } from "./EpisodeList";
import { Notice } from "./Notice";
import { PodcastHeader } from "./PodcastHeader";

/** Server component: fetches and renders a show and its newest episodes. */
export async function PodcastView({ podcastId }: { podcastId: string }) {
  let data;
  try {
    data = await getPodcastEpisodes(podcastId);
  } catch (err) {
    if (err instanceof ApiError && (err.status === 404 || err.status === 422)) {
      return (
        <Notice title="We don't know this show yet" action={{ href: "/", label: "Search for it" }}>
          <p>
            PodderSum learns about a show the first time it appears in a search. Search for this
            show first, then open it from the results.
          </p>
        </Notice>
      );
    }
    const message = err instanceof ApiError ? err.detail : "Something went wrong.";
    return (
      <Notice title="Could not load this show" tone="error" action={{ href: "/", label: "Back to search" }}>
        <p>{message}</p>
      </Notice>
    );
  }

  const { podcast, episodes, total } = data;
  return (
    <div className="space-y-8">
      <PodcastHeader podcast={podcast} />
      <section aria-labelledby="episodes-heading" className="space-y-3">
        <div className="flex items-baseline justify-between gap-4">
          <h2 id="episodes-heading" className="font-display text-xl font-semibold">
            Episodes
          </h2>
          <p className="text-sm text-ink-muted">
            {episodes.length < total
              ? `Newest ${episodes.length} of ${total}`
              : `${total} ${total === 1 ? "episode" : "episodes"}`}
          </p>
        </div>
        {episodes.length === 0 ? (
          <Notice title="No episodes with audio">
            <p>This feed has no episodes with an audio file yet.</p>
          </Notice>
        ) : (
          <EpisodeList episodes={episodes} />
        )}
      </section>
    </div>
  );
}
