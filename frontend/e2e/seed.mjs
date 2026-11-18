/** Seeded episode shared by the mock API and the Playwright spec. */
const hex = (c) => c.repeat(64);

const podcast = {
  id: hex("a"),
  title: "Hard Fork",
  author: "The New York Times",
  artwork_url: null,
  feed_url: "https://feeds.example.com/hardfork",
  genre: "Technology",
  itunes_id: 1528594034,
};

const cite = (startMs, quote) => [
  { segment_id: 1 + Math.floor(startMs / 1000), start_ms: startMs, end_ms: startMs + 8000, speaker: "Kevin", quote },
];

export const SEED = {
  episode: {
    id: hex("c"),
    podcast_id: podcast.id,
    podcast,
    title: "The AI Episode",
    published_at: "2026-11-03T10:00:00Z",
    duration_s: 3725,
    description: "We talk about AI.",
    artwork_url: null,
    has_feed_transcript: true,
    status: "ready",
    guid: "guid-1",
    audio_url: "https://media.example.com/ep1.mp3",
    audio_type: "audio/mpeg",
    transcript_tags: [],
    active_job_id: null,
  },
  artifacts: {
    verdict: [
      { text: "Founders building AI products", citations: cite(30_000, "if you are building"), section: "who_its_for" },
      { text: "A first look at the new agent rules", citations: cite(600_000, "the new rules"), section: "whats_new" },
    ],
    tldr: [{ text: "They debate how AI regulation will land", citations: cite(120_000, "regulation"), section: null }],
    takeaways: [{ text: "Regulation is coming sooner than expected", citations: cite(900_000, "sooner"), section: null }],
  },
  askClaims: [
    { text: "Ship before you scale", citations: cite(62_400, "ship it"), section: null },
    { text: "Hire for curiosity", citations: cite(3_661_000, "curious people"), section: null },
  ],
};
