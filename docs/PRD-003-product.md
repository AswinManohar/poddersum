# PodderSum Product Requirements Document

Nov 10, 2026 · @Aswin Manohar

## Summary

PodderSum becomes a hosted web product where anyone types a podcast name, picks an episode, and gets a summary shaped by what they ask for. Every answer links back to the moment in the audio it came from.

Today PodderSum is a single-user Streamlit app tied to a local gPodder database. It sends raw audio to Gemini for every summary and every chat question. The product version changes three things:

1. **Discovery:** search any public podcast by name instead of reading a local gPodder database.
2. **Transcript-first processing:** each episode is transcribed once, stored, and shared across all users. Summaries, takeaways and chat all read the stored text.
3. **Multi-user platform:** accounts, usage limits, billing, background jobs and a real web front end.

The differentiator is question-driven and cross-episode answers: "summarize this for me, about X" and "what has this host said about X across every episode". Generic recaps are already offered by existing apps.

## Goals, non-goals and success metrics

**Goals**

- A new user gets a useful, personalized summary of any public podcast episode within 2 minutes of landing, without installing anything.
- Every claim in a summary or answer carries a timestamp link to the source audio.
- The cost of a cached episode answer stays under $0.01, so a free tier is affordable.
- The transcript library compounds: each processed episode makes cross-episode search more valuable.

**Non-goals for v1**

- Hosting or streaming audio. PodderSum links out to the original episode.
- Publishing full transcripts publicly.
- Native mobile apps. The web app is responsive; native comes after product-market fit.
- Video-only content with no audio feed.

**Success metrics**

| Metric | Target at 3 months after launch |
| --- | --- |
| Time from search to first summary (cached episode) | Under 10 seconds |
| Time from search to first summary (new 1-hour episode) | Under 3 minutes |
| Week-4 retention of signed-up users | 25% |
| Free to paid conversion | 3% |
| Summary factual-alignment score (DeepEval) | 0.85 or higher |
| Share of requests served from the transcript cache | 70% |
| Gross margin on paid plans | 75% |

The targets are starting assumptions to revise once real usage data exists.

## Users and their jobs

| User | What they want | What they use most |
| --- | --- | --- |
| Busy listener | Decide whether a 2-hour episode is worth it, or get the point without listening | Worth-it verdict, TL;DR, jump-to-moment links |
| Power learner | Remember and apply what they hear across many shows | Standing profile, action items, flashcards, weekly recap, cross-episode search |
| Researcher or analyst | Track what specific people and shows say about a topic | Ask a whole show, guest tracker, disagreement map |
| Podcast creator (second customer group) | Show notes, chapters and newsletter drafts from their own episodes | Creator workspace |

## Features

P0 is the launch product, P1 is the differentiator built in the following quarter, and P2 is growth and the creator side.

### Core (P0)

| Feature | What the user gets | Acceptance criteria |
| --- | --- | --- |
| Podcast search | Type any show name and see matching shows and their episodes | Results in under 1 second; covers any show with a public RSS feed |
| Ask-anything summary | A free-text box: "summarize for a startup founder", "only the parts about sleep" | Answer streams within 5 seconds on a cached episode; follows the stated angle |
| Preset views | TL;DR, key takeaways, action items, quotes, full breakdown (today's template), explain-like-I'm-new | Each preset is one click; results are cached per episode |
| Jump-to-the-moment links | Every takeaway and quote has a timestamp that opens the episode at that point | 95% of timestamps land within 15 seconds of the cited passage |
| Episode chat | Follow-up questions answered from the stored transcript, with citations | Works on episodes of any age; no audio re-upload |
| "Is this worth my time?" | A 30-second verdict: who it is for, what is new, the best 10 minutes | Shown before the full summary |
| Accounts and usage limits | Sign in, history of past summaries, free monthly quota | Quota enforced per account; history persists |

### Personalization (P1)

| Feature | What the user gets | Acceptance criteria |
| --- | --- | --- |
| Standing profile | Role and interests stored once; every summary is written through that lens | Profile editable; can be switched off per request |
| Skip what I know | Summaries flag what repeats episodes the user already read and highlight what is new | Uses the user's history only; shows which earlier episode overlaps |
| Follow a show and daily briefing | One morning digest across all new episodes from followed shows, filtered by interests | Email and in-app; delivered within 6 hours of episode release |
| Action items export | Checklist from advice-heavy episodes, sent to Notion or Todoist | One-click export; items keep their timestamp links |

### Cross-episode intelligence (P1, the moat)

| Feature | What the user gets | Acceptance criteria |
| --- | --- | --- |
| Ask a whole show or all shows | "What has this host said about creatine across every episode?" | Answer cites episode and timestamp for each claim; searches the full shared library |
| Guest tracker | Follow a person; get notified when they appear on any indexed podcast, with what they said | Entity resolution merges name variants; alert within 24 hours |
| Disagreement map | Positions of different guests on one topic, side by side | Each position quoted and cited; at least 2 sources per map |
| Running library | Books, papers and tools aggregated across followed shows, ranked by mentions | Deduplicated; each item links to the episodes that mention it |

### Retention (P2)

| Feature | What the user gets | Acceptance criteria |
| --- | --- | --- |
| Flashcards and quizzes | Cards generated from takeaways, with spaced repetition by email | Each card cites its episode moment |
| Weekly recap | What the user learned this week across everything they summarized | Sent weekly; one-click unsubscribe |

### Growth and creators (P2)

| Feature | What the user gets | Acceptance criteria |
| --- | --- | --- |
| Shareable quote cards and summary pages | Public page or image for one takeaway, linking back to the episode and to PodderSum | Short excerpts only; indexable by search engines |
| Creator workspace | Show notes, chapter markers and newsletter drafts for the creator's own episodes | Creator verifies feed ownership; exports to Markdown |
| Browser extension | Summarize the episode open in the current tab | Works on Apple Podcasts, Spotify web and YouTube pages |

## System architecture

The system has a serving path that answers from stored text and an ingestion path that runs once per episode as a background job.

&#91;embedded content: system architecture · serving, ingestion, shared stores\]

The top row serves requests: the answer engine retrieves segments from Postgres and calls a model through the gateway. The middle row runs once per episode. The highlighted step is the shared transcript cache that keeps costs low.

**Request flow**

1. The user searches; the API queries the podcast directory and returns shows and episodes.
2. The user picks an episode and types a request. The API computes the episode id and checks for a stored transcript.
3. If the transcript exists, the answer engine retrieves the relevant segments and streams a cited answer.
4. If not, the API takes a lock, enqueues a workflow, and streams progress to the browser.
5. The workflow reads the feed's transcript tag if present. Otherwise it downloads the audio, transcribes it, and deletes the audio.
6. The transcript is split into speaker-aware segments with timestamps, embedded, and scanned for entities and resources.
7. The pending request is answered, and preset views are generated and cached for the next user.

**Retrieval design**

- Single-episode requests under about 30,000 tokens send the whole transcript with prompt caching; longer ones use retrieval.
- Cross-episode requests use hybrid search (vector plus keyword), filter by show or entity, rerank, and pass the top segments with their episode and timestamp.
- The user's standing profile is added to the prompt as the lens for the answer.

## Tech stack

The stack keeps Python for the pipeline so the existing LangGraph and Gemini code carries over, and uses managed services so one developer can run it.

| Layer | Choice | Why | When to change |
| --- | --- | --- | --- |
| Web front end | Next.js (React, TypeScript) on Vercel, Tailwind, streaming responses over SSE | Fast first load, public summary pages render on the server for search traffic | Stays |
| API | FastAPI (Python 3.12), Pydantic models, async endpoints | Reuses the current Python code; typed contracts for the front end | Stays |
| Background workflows | Temporal Cloud or Inngest for durable, retried, resumable jobs | A 1-hour transcription must survive restarts and vendor timeouts; steps are retried individually | Celery + Redis is a simpler start if cost matters |
| Chat agent | LangGraph with the Postgres checkpointer | Already built; only the checkpoint store changes from SQLite | Stays |
| Primary database | Postgres (Neon or Supabase) | Users, episodes, transcripts, chats, billing state in one place; branching for safe migrations | Stays |
| Vector and keyword search | pgvector with HNSW index plus Postgres full-text search, combined as hybrid search with a reranker | One database to run; hybrid search finds names and exact phrases that embeddings miss | Move chunks to Qdrant or Turbopuffer past roughly 50 million chunks |
| Object storage | Cloudflare R2 | Stores audio temporarily and raw transcripts; no egress fees | Stays |
| Cache and rate limits | Redis (Upstash) | Per-user quotas, hot summaries, job de-duplication locks | Stays |
| Podcast discovery | iTunes Search API for name matching, Podcast Index API for feed data and transcript tags | Both free; together they cover nearly every public feed | Add Listen Notes if metadata gaps appear |
| Auth | Clerk (or Supabase Auth) | Social login, sessions and organization accounts without custom code | Stays |
| Billing | Stripe Billing with usage metering | Subscriptions plus per-episode overage | Stays |
| Model gateway | LiteLLM (self-hosted) or a hosted gateway | One interface to Gemini, Claude and others; per-task routing, fallbacks, spend caps | Stays |
| Observability | LangSmith (already traced in the code) or Langfuse for LLM traces; Sentry for errors; PostHog for product analytics | Cost and quality per feature are visible from day one | Stays |
| Workers hosting | Fly.io or Railway at launch; Google Cloud Run or Kubernetes at scale | Simple deploys first, autoscaling on queue depth later | When queue wait exceeds targets |
| Email | Resend | Daily briefing, weekly recap, guest alerts | Stays |

**Scaling rules built in from the start**

- **Process once, serve many.** An episode is keyed by a hash of feed URL plus episode guid. A lock in Redis stops two users triggering the same transcription.
- **Everything slow is a job.** The API never waits on transcription; it returns a job id and streams progress.
- **Stateless services.** API and workers hold no local files, so they scale horizontally. The hardcoded `/home/aswinmanohar/...` paths are removed.
- **Batch where the user is not waiting.** Followed-show ingestion and evaluations run through batch APIs at about half price.

## Model recommendations

Use a dedicated speech-to-text model for transcripts, a cheap fast model for most answers, and a stronger model only for cross-episode synthesis and evaluation. Model names live in one config file behind the gateway, because lineups and prices change every few months.

| Task | Recommended | List price | Why | Alternative |
| --- | --- | --- | --- | --- |
| Transcription with speakers and word timestamps | [AssemblyAI Universal-3.5 Pro](https://aquavoice.com/blog/best-speech-to-text-api) (batch) | $0.21 per audio hour, diarization is a small add-on | Word-level timestamps make jump-to-moment links accurate; no output-length limit on long episodes | Deepgram Nova-3 at $0.26 per hour; ElevenLabs Scribe v2 at $0.22 per hour |
| Low-cost transcription for the long tail | [Gemini 3.1 Flash-Lite](https://ai.google.dev/gemini-api/docs/pricing), audio in 15-minute chunks | About $0.07 per audio hour ($0.50 per 1M audio tokens in, $1.50 out) | A third of the price; uses host and guest names from the intro as speaker labels | Use for free-tier requests on rarely requested episodes |
| Default summaries, presets, episode chat | [Gemini 3.1 Flash-Lite](https://ai.google.dev/gemini-api/docs/pricing) | $0.25 in, $1.50 out per 1M tokens | A full transcript summary costs under 1 cent; fast enough to stream | Gemini 3.5 Flash ($1.50 in, $9.00 out) when quality scores fall short |
| Cross-episode answers, disagreement map, skip-what-I-know | Claude Sonnet tier or Gemini 3.1 Pro | [Sonnet tier $3 in, $15 out](https://www.g2.com/articles/claude-api-pricing); [Gemini 3.1 Pro $2 in, $12 out](https://ai.google.dev/gemini-api/docs/pricing) per 1M tokens | Synthesis across many sources with careful citation needs a stronger model | Route only paid-plan requests here |
| Entity and resource extraction | Gemini 3.1 Flash-Lite with structured JSON output, via batch | Half the standard rate in batch | Runs once per episode, offline | Same model as default |
| Embeddings | [EmbeddingGemma 2](https://ai.google.dev/gemma/docs/embeddinggemma), self-hosted, text encoder only | Free open weights (Apache 2.0); pay only for compute | No per-token fee as the library grows; 768-dimension vectors that shorten to 256 to cut storage; 8,192-token context; 100+ languages; runs on CPU or a small GPU | Gemini Embedding 2 (hosted, $0.20 per 1M text tokens) if running a model server is not wanted. Compare both on 200 real podcast questions before indexing, because switching later means re-embedding everything |
| Reranking | Cohere Rerank or Voyage rerank | Per-search pricing | Lifts retrieval precision on long transcripts | Skip at launch; add when cross-episode search ships |
| Evaluation judge | A different, stronger model than the generator (Claude Opus tier), via batch | [Opus tier $5 in, $25 out](https://www.g2.com/articles/claude-api-pricing), 50% off in batch | A judge from another vendor avoids a model grading its own style | Sample 5% of outputs, not all |

**Cost of one 1-hour episode (approximate)**

| Step | Tokens or units | Cost |
| --- | --- | --- |
| Transcription (AssemblyAI) | 1 audio hour | $0.21 |
| Embedding the transcript | About 15,000 tokens | Under $0.001 (self-hosted compute) |
| Default summary (Flash-Lite) | About 15,000 in, 1,500 out | $0.006 |
| Entity and resource extraction | About 15,000 in, 1,500 out | $0.006 |
| **First-time total, paid once per episode for all users** |  | **About $0.23** |
| Each later question (retrieval, Flash-Lite) | About 4,000 in, 400 out | $0.002 |
| Each later question (Sonnet tier) | About 4,000 in, 400 out | $0.018 |

These costs compare with the current design, where every chat question re-sends about 90,000 audio tokens (1 hour at 25 tokens per second).

**Notes on the figures**

- Prices are list prices found on 10 November 2026. Gemini figures come from Google's pricing page. Speech-to-text and Claude figures come from third-party roundups, because the vendors' own pricing pages could not be opened; confirm them before budgeting.
- The Claude prices found were listed for Sonnet 5, Opus 4.8 and Haiku 4.5. Check current rates for the newest Sonnet and Opus models.
- Some third-party listings mention newer Gemini models (3.8 Flash, 4 Argon). They did not appear in the part of Google's pricing page that was read, so they are not recommended here. Re-check before launch.
- The repo currently calls `gemini-2.0-flash`. Move to a current model as part of Phase 1. EmbeddingGemma 2 must not be loaded in float16: its model card says that returns degraded embeddings without an error.

## Data model

Shared content (shows, episodes, transcripts, chunks, entities) is stored once for everyone; user data points at it.

| Table | Key fields | Notes |
| --- | --- | --- |
| `podcasts` | id, feed\_url (unique), title, artwork\_url, directory ids | Created on first search hit that a user opens |
| `episodes` | id = hash(feed\_url + guid), podcast\_id, title, published\_at, audio\_url, mime\_type, duration | The hash replaces gPodder's row id, so the same episode has the same id for every user |
| `transcripts` | episode\_id, source (feed tag, speech-to-text vendor), status, language, storage\_key, model, cost | One per episode; raw file in object storage |
| `segments` | id, episode\_id, speaker, start\_ms, end\_ms, text, embedding, tsvector | The retrieval unit; about 200 to 400 tokens with speaker turns kept whole |
| `entities` | id, type (person, company, book, paper, tool, place), canonical\_name, aliases | Deduplicated across shows; powers guest tracker and running library |
| `mentions` | entity\_id, episode\_id, segment\_id, start\_ms | Links an entity to the exact moment |
| `artifacts` | episode\_id, kind (tldr, takeaways, verdict, breakdown), prompt\_version, content, model | Cached preset outputs, shared across users |
| `users` | id, auth id, plan, profile (role, interests), quotas | Profile feeds the standing lens |
| `requests` | id, user\_id, episode\_id or scope, question, answer, citations, tokens, cost, model | Per-user history; replaces the per-thread usage list in `AgentState` |
| `follows` | user\_id, podcast\_id or entity\_id, notify settings | Drives the briefing and guest alerts |
| `cards` | user\_id, source request, front, back, next\_review\_at | Flashcards with spaced repetition |
| `jobs` | id, episode\_id, step, status, attempts, error | Mirrors workflow state for the progress UI |

## Quality and evaluation

Every answer must be traceable to the transcript, and quality is measured continuously with the DeepEval plan already written in PRD-002.

- **Grounding by design.** The answer prompt receives transcript segments with ids and timestamps and must cite a segment for each claim. Claims without a citation are dropped before display.
- **Offline evaluation (ISSUE-004 to 006).** DeepEval summarization and faithfulness metrics run against stored transcripts, with a pass threshold of 0.85. A fixed set of 50 episodes across genres is the regression suite for every prompt or model change.
- **Online sampling.** 5% of production answers are scored by the judge model in batch. Scores are tracked per feature and per model in LangSmith or Langfuse.
- **Timestamp accuracy.** A weekly check plays 100 random citations against the transcript window and reports the share within 15 seconds.
- **Transcript quality.** Word error rate is spot-checked on 20 episodes per vendor before choosing the default, including accented speech and crosstalk.
- **User feedback.** Thumbs up or down on each answer, plus "this citation is wrong"; negative items join the regression suite.
- **Prompt versions.** Each cached artifact stores its prompt version, so a prompt change can re-generate only affected items.

## Pricing and unit economics

A free tier is affordable because cached episodes cost about $0.002 per question; the paid plan covers new transcriptions and stronger models.

| Plan | Price (proposed) | Includes |
| --- | --- | --- |
| Free | $0 | Unlimited presets on already-processed episodes, 3 new episodes a month, 20 questions a month |
| Plus | $10 a month | 40 new episodes a month, standing profile, follows and daily briefing, exports, flashcards |
| Pro | $25 a month | 150 new episodes a month, cross-episode search, guest tracker, disagreement map, stronger model |
| Creator | $29 a month per show | Show notes, chapters and newsletter drafts for own episodes |

A Plus user who triggers 40 new episodes that nobody else has requested costs about $9.20 in transcription at the top of the range. In practice many requests hit the cache, and the low-cost transcription path brings a new episode to about $0.09. Monitor the cache hit rate and new-episode cost per paying user weekly, and adjust the quota before changing the price.

Prices are proposals to test, not validated willingness to pay.

## Legal, privacy and content rights

Summaries and question answering carry lower risk than republishing; the product is designed to stay on the lower-risk side. This section is not legal advice, and a lawyer should review it before charging money.

- **No public full transcripts and no audio hosting.** Transcripts stay internal. Downloaded audio is deleted after processing.
- **Short excerpts with attribution.** Quotes are brief, attributed, and link to the original episode at the timestamp.
- **Drive traffic back.** Every summary links to the show's own page or feed.
- **Opt-out.** A published process lets a podcast owner remove their show; removals take effect within 48 hours.
- **Creator features need proof of ownership.** Verification by a code placed in the feed or email to the feed's owner address.
- **API terms.** Follow the terms of iTunes Search, Podcast Index and each model vendor, including attribution and caching limits.
- **User privacy.** Profiles and histories are private by default, exportable and deletable. User data is not sent to vendors that train on it.
- **Compliance.** GDPR and CCPA requests handled through account settings; data processing agreements signed with each vendor.

## Roadmap

Five phases take the current repo to a paid product; each ends at a gate that must pass before the next starts. Durations assume one developer and are estimates.

1. **Phase 1: Product core in the current app (2 to 3 weeks).** Replace `gpodder_utils.py` with a discovery module (search, feed parsing, transcript tag). Use hashed episode ids and one shared thread per episode. Store transcripts and answer from text. Add the free-text request box and presets. Remove hardcoded paths and fix `pyproject.toml`.
   - Gate: any public podcast can be searched and summarized on a clean machine; a repeat request costs under 1 cent.
2. **Phase 2: Pipeline and quality (3 weeks).** Dedicated speech-to-text with word timestamps, chunking and embeddings in Postgres with pgvector, durable workflows, timestamp citations, DeepEval suite (ISSUE-004 to 006).
   - Gate: faithfulness 0.85 or higher on the 50-episode suite; 95% of timestamps within 15 seconds.
3. **Phase 3: Web product and launch (4 weeks).** Next.js front end, FastAPI service, accounts, quotas, Stripe, worth-it verdict, history, opt-out process.
   - Gate: 100 beta users; first summary under 3 minutes for a new episode; legal review complete.
4. **Phase 4: Personalization and cross-episode intelligence (6 weeks).** Standing profile, follows and daily briefing, entity resolution, ask-a-whole-show, guest tracker, running library, disagreement map, skip-what-I-know, exports.
   - Gate: week-4 retention of 25%; cross-episode answers pass the citation check.
5. **Phase 5: Retention, growth and creators (ongoing).** Flashcards, weekly recap, shareable pages and quote cards, creator workspace, browser extension.
   - Gate: free-to-paid conversion of 3% and gross margin of 75%.

## Risks and open questions

| Risk | Effect | Mitigation |
| --- | --- | --- |
| A rights holder objects to summaries | Takedown requests or blocked feeds | Opt-out process, short excerpts, links back, legal review before launch |
| Transcription cost on long-tail episodes | Margin falls if cache hit rate is low | Low-cost path for free tier, quotas on new episodes, feed transcripts first |
| Hallucinated or misattributed claims | Loss of trust | Citation-required answers, judge sampling, feedback loop |
| Speaker labels wrong | Guest tracker and disagreement map attribute quotes to the wrong person | Use episode metadata to name speakers; show low-confidence labels as "Speaker" |
| Existing apps add the same features | Weaker differentiation | Prioritize cross-episode intelligence, which depends on the accumulated library |
| Vendor price or model changes | Cost or quality shifts | Gateway with per-task routing and fallbacks; regression suite on every switch |
| Dynamic ad insertion shifts audio timing | Timestamps drift between downloads | Store the processed file's duration; link by nearest transcript text when durations differ |

**Open questions**

- [ ] Which languages at launch: English only, or multilingual from the start?
- [ ] Should YouTube captions be used as a transcript source, given the platform's terms?
- [ ] Is the first paying customer the listener or the creator?
- [ ] Does the product keep the PodderSum name?
- [ ] Where are users located, and does that require EU data residency?

## Sources

- [Gemini Developer API pricing](https://ai.google.dev/gemini-api/docs/pricing), Google, read 10 November 2026
- [Best Speech to Text APIs (2026), Compared](https://aquavoice.com/blog/best-speech-to-text-api), Aqua Voice, prices checked 28 October 2026
- [Best real-time speech-to-text APIs in 2026](https://www.assemblyai.com/blog/best-api-models-for-real-time-speech-recognition-and-transcription), AssemblyAI
- [Claude API pricing](https://www.g2.com/articles/claude-api-pricing), G2
- [PodderSum repository](https://github.com/AswinManohar/poddersum), reviewed 10 November 2026
