import type { EpisodeDetail, EpisodeRef, PodcastHit } from "@/lib/models";

const hex = (c: string) => c.repeat(64);

export const podcast: PodcastHit = {
  id: hex("a"),
  title: "Hard Fork",
  author: "The New York Times",
  artwork_url: "https://img.example.com/hf.jpg",
  feed_url: "https://feeds.example.com/hardfork",
  genre: "Technology",
  itunes_id: 1528594034,
};

export const otherPodcast: PodcastHit = {
  ...podcast,
  id: hex("b"),
  title: "Hard Knocks Radio",
  author: null,
  artwork_url: null,
  genre: null,
};

export function episodeRef(overrides: Partial<EpisodeRef> = {}): EpisodeRef {
  return {
    id: hex("c"),
    podcast_id: podcast.id,
    title: "The AI Episode",
    published_at: "2026-11-03T10:00:00Z",
    duration_s: 3725,
    description: "We talk about AI.",
    artwork_url: null,
    has_feed_transcript: false,
    status: "new",
    ...overrides,
  };
}

export const episodeDetail: EpisodeDetail = {
  ...episodeRef(),
  podcast,
  guid: "guid-1",
  audio_url: "https://media.example.com/ep1.mp3",
  audio_type: "audio/mpeg",
  transcript_tags: [],
  active_job_id: null,
};
