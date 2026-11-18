# PodderSum Implementation Plan

Source of truth: `docs/PRD-003-product.md` (Nov 10, 2026). Plan written 2026-11-12. Only the orchestrator edits this file.

- **Build order: Milestone 0 (prototype) first**, then the rest of Phases 1–3 (through launch) in full slice detail. Phases 4–5: outline.
- Every slice is a tracer bullet: end to end, demoable, tested. Tick the slice box only when every acceptance box under it is ticked.
- Milestone 0 and Phase 1 need no new account. They run on the existing `GEMINI_API_KEY` + `LANGSMITH_*` keys and local docker.
- Front end uses **npm** (pnpm is not installed). Python 3.12 comes from `uv python install 3.12`.

## 0. How to read this plan

**PRD acceptance tags used below**

| Tag | PRD criterion |
|---|---|
| AC-SEARCH | Results in under 1 s; covers any show with a public RSS feed |
| AC-ASK | Answer streams within 5 s on a cached episode; follows the stated angle |
| AC-PRESET | Each preset is one click; results cached per episode |
| AC-JUMP | 95% of timestamps land within 15 s of the cited passage |
| AC-CHAT | Works on episodes of any age; no audio re-upload |
| AC-WORTH | Verdict (who it is for, what is new, best 10 minutes) shown before the full summary |
| AC-ACCT | Quota enforced per account; history persists |
| G1 | Any public podcast searched and summarized on a clean machine; repeat request under 1 cent |
| G2 | Faithfulness ≥ 0.85 on the 50-episode suite; 95% of timestamps within 15 s |
| G3 | 100 beta users; first summary under 3 min for a new episode; legal review complete |

**Shared contracts (sequenced, never edited by two slices at once)**

| Contract | Owner who writes it | Rule |
|---|---|---|
| `backend/api/schemas.py` (+ mirror `frontend/lib/types.ts`) | backend-engineer proposes, orchestrator sequences | One open schema change at a time; consumer slices start after it merges |
| `backend/db/migrations/` (Alembic) | backend-engineer | Revision numbers are pre-assigned in this plan (0001…); merge strictly in order |
| `backend/llm/models.yaml` | langgraph-engineer | Task keys: `summary`, `chat`, `stt_cheap`, `extraction`, `embedding`, `cross_episode`, `judge`, `rerank`. No model id anywhere else |
| `backend/core/settings.py`, `.env.example` | env-config-manager | Other agents report new variables; they do not edit these files |

Slices marked **Contract** change one of these. Where a slice needs a contract change owned by another agent, it is listed as a "contract pre-step" and runs first.

**Legacy issue mapping**

| Old item | Status in this plan |
|---|---|
| PRD-001, ISSUE-001 (transcribe node) | Superseded by M0-S4 (transcript is always produced and stored; no toggle) |
| ISSUE-002 (Markdown export, batch) | Dropped: no local files. Batch ingestion returns in Phase 4 (follows) |
| ISSUE-003 (Streamlit transcript toggle) | Dropped: the Next.js prototype replaces Streamlit; transcripts stay internal |
| PRD-002, ISSUE-004 | P2-S6 (moved from `tests/` to `backend/evals/`) |
| ISSUE-005 | P2-S7 (threshold raised from 0.8 to 0.85 per PRD-003) |
| ISSUE-006 | P2-S8 |

## M0. Milestone 0 — Prototype (build this first)

Goal: a simple working product on one machine. Search any podcast by name → list episodes from RSS → transcript-first (feed `<podcast:transcript>` tag, else Gemini audio transcription with timestamps) → transcript stored in local Postgres → ask-anything box and 3 presets (TL;DR, takeaways, worth-it) answered from stored text with timestamp citations → simple chat. Minimal FastAPI backend and minimal Next.js front end with SSE streaming.

| In scope | Deferred (phase that adds it) |
|---|---|
| iTunes Search (no key), RSS parsing, hashed episode ids | Podcast Index, Redis search cache (Phase 1) |
| Feed transcript tag, else Gemini chunked transcription | AssemblyAI word timestamps (Phase 2) |
| Whole transcript in the prompt | pgvector, hybrid retrieval (Phase 2) |
| In-process background job with SSE progress, Postgres advisory lock | Redis lock, object storage (Phase 1); durable Inngest workflow (Phase 2) |
| 3 presets cached in `artifacts`, ask-anything, chat | Remaining 4 presets (Phase 1); evals (Phase 2) |
| Single user, no auth, no billing, LangSmith tracing on | Accounts, quotas, Stripe, opt-out, deploy (Phase 3) |

**Keys:** `GEMINI_API_KEY` + `LANGSMITH_*` (both already in `.env`). No new accounts.
**Local tooling (verified 2026-11-12):** uv 0.6.12; system Python is 3.10, so run `uv python install 3.12` and pin 3.12 in `backend/`; Docker works; Node 26 with npm 11 (**use npm, not pnpm**). `ffmpeg` is not installed: the prototype uses the binary bundled by the `imageio-ffmpeg` package, so no system install is needed.
**Legacy code:** `app.py`, `graph_engine.py` and the other root files stay untouched during M0 and are deleted in P1-S5. The Streamlit harness is not carried forward; the Next.js page is the UI from day one (orchestrator updates `CLAUDE.md`: harness row, `pnpm` → `npm`).

**Order and parallelism**

| Wave | Slices | Notes |
|---|---|---|
| 1 | M0-S0 | Blocks everything |
| 2 | M0-S1 ∥ M0-S2 | S1 freezes the prototype API contract (schemas v0) and migration 0001; S2 creates models.yaml |
| 3 | M0-S3 ∥ M0-S4 | Front end builds against the frozen contract while ingest lands (migration 0002) |
| 4 | M0-S5 | Migration 0003 pre-step, then the answer engine |
| 5 | M0-S6 ∥ M0-S7 (ask and presets part) | Chat graph and episode page in parallel; S7 adds the chat panel when S6 merges |
| 6 | M0 demo check (inside M0-S7) | |

### [x] M0-S0 — Scaffold, settings and local Postgres
- **Owner:** env-config-manager · **Depends on:** none · **Contract:** creates `settings.py`, `.env.example`
- **Files:** `backend/pyproject.toml` (Python 3.12, dependencies, ruff, pytest), `backend/.python-version`, `backend/core/settings.py`, `backend/api/main.py` (`/healthz` only), `.env.example`, `frontend/.env.example`, `infra/docker-compose.yml` (Postgres using the pgvector image so Phase 2 needs no image change), `.gitignore`, `backend/tests/test_smoke.py`
- **Acceptance**
  - [x] `uv python install 3.12`, `docker compose -f infra/docker-compose.yml up -d`, `cd backend && uv sync && uv run pytest` pass on this machine
  - [x] `uv run uvicorn api.main:app` serves `/healthz`; settings fail fast naming any missing required variable
  - [x] `.gitignore` covers `.env*` (not examples), `*.db`, `downloads/`, `summaries/`, `transcriptions/`, `eval_reports/`, `node_modules/`, `.next/`
- **Tests:** settings load; database connectivity
- **Env:** `GEMINI_API_KEY`, `LANGSMITH_*` (exist); new local `DATABASE_URL`, `APP_ENV`, `NEXT_PUBLIC_API_BASE_URL`
- **Done 2026-11-12.** Local ports: Postgres on host **5433** (5432 is taken on this machine), API on **8010** (8000 is taken). `DATABASE_URL` defaults to the docker Postgres when `APP_ENV=local`. Follow-up: translate `?sslmode=` for asyncpg before the staging deploy (P3-S2).

### [x] M0-S1 — Search and episode list API, prototype contract v0
- **Owner:** backend-engineer · **Depends on:** M0-S0 · **Parallel with:** M0-S2 · **Contract:** migration `0001` (`podcasts`, `episodes`); `schemas.py` v0 covering every prototype route and SSE event (`PodcastHit`, `EpisodeRef`, `EpisodeDetail`, `JobStatus`, `JobEvent`, `AskRequest`, `Answer`, `Claim`, `Citation`, `Artifact`, `ChatMessage`); `frontend/lib/types.ts` generated from OpenAPI
- **Files:** `backend/discovery/` (`itunes.py`, `feed.py`, `ids.py`), `backend/db/` (models, session, migration `0001_*`), `backend/api/schemas.py`, `backend/api/routes/` (`search.py`, `podcasts.py`, `episodes.py`), `backend/scripts/export_openapi.py`
- **Build:** `GET /search?q=`, `GET /podcasts/{id}/episodes`, `GET /episodes/{id}`; iTunes Search for names; `feedparser` for episodes, enclosure, duration and the transcript tag; `episode_id = sha256(normalized_feed_url + guid)`; routes for ingest, ask, artifacts and chat declared as stubs so the contract is complete
- **Acceptance**
  - [x] Searching a show name returns shows, then its episodes, with no gPodder database present (AC-SEARCH coverage)
  - [x] Same episode gets the same id across runs; normalization rules documented in `ids.py`
  - [x] `types.ts` is generated, not hand-written, and CORS allows the local web origin
- **Tests:** recorded iTunes and RSS fixtures (transcript tag present, missing guid, redirect); id determinism; route contract tests
- **Env:** none
- **Done 2026-11-12.** Search 0.1–0.6 s live. Responses are wrapped (`SearchResponse`, `PodcastEpisodes`). SSE routes are documented through `api/openapi.py` (`sse_doc`). Regenerate `types.ts` with the command in the header of `backend/scripts/export_openapi.py`. Follow-ups: `/podcasts/{id}/episodes` needs the show to have come through `/search` first; the feed is refetched on every list request (cache in P1-S2/P1-S4); Alembic needs `GEMINI_API_KEY` set because it loads full settings.

### [ ] M0-S2 — Model gateway, `models.yaml` and tracing
- **Owner:** langgraph-engineer · **Depends on:** M0-S0 · **Parallel with:** M0-S1 · **Contract:** creates `models.yaml`
- **Files:** `backend/llm/gateway.py`, `backend/llm/models.yaml`, `backend/llm/pricing.py`, `backend/llm/smoke.py`, `backend/core/tracing.py`
- **Build:** LiteLLM library behind `complete()`, `stream()`, `structured()`, `transcribe_audio()`; each call returns text plus tokens, cost and model; task keys `summary`, `chat`, `stt_cheap` map to Gemini 3.1 Flash-Lite (confirm the exact id against the provider's model list); LangSmith tags `feature`, `model`, `prompt_version`, `episode_id`
- **Acceptance**
  - [ ] `uv run python -m llm.smoke "hello"` prints text, tokens and cost, and the run appears in LangSmith
  - [x] `grep -rn "gemini-" backend --include=*.py` returns nothing
  - [x] Tracing is skipped silently when `LANGSMITH_*` is unset
- **Tests:** gateway with a fake provider; cost arithmetic; unknown task key raises
- **Env:** `GEMINI_API_KEY`, `LANGSMITH_*`
- **Awaiting user review** (the user reviews `backend/llm/` and `backend/agent/` code personally before these slices are closed).
- **Built 2026-11-12, live Gemini call NOT verified.** The `GEMINI_API_KEY` in `.env` is rejected by the API (`API_KEY_INVALID`), so the first box stays open; a fake-provider run did appear in LangSmith. The model id `gemini-3.1-flash-lite` and its prices come from Google's public docs and are marked PROVISIONAL in `models.yaml`. After replacing the key: `cd backend && uv run python -m llm.smoke --list-models && uv run python -m llm.smoke "hello"`. **Re-checked 2026-11-18: key still rejected (`API_KEY_INVALID`); the key is present in `.env` and well formed, and no shell variable overrides it. Box 1 stays open.** Rule added: never import `litellm` outside `backend/llm/` (its import otherwise exports `.env` into `os.environ`).

### [x] M0-S3 — Web shell: search to episode page
- **Owner:** frontend-engineer · **Depends on:** M0-S1 · **Parallel with:** M0-S4
- **Files:** `frontend/` scaffold (Next.js App Router, TypeScript strict, Tailwind, npm), `frontend/app/page.tsx` (search), `frontend/app/podcast/[id]/page.tsx`, `frontend/app/episode/[id]/page.tsx` (metadata only), `frontend/lib/api.ts`, `frontend/lib/types.ts`
- **Acceptance**
  - [x] `npm install && npm run dev`: type a show name, see shows, open one, see its episodes, open an episode page with a link to the original audio
  - [x] `npm run lint && npm run typecheck && npm test` pass; no `any`
- **Tests:** component tests with a mocked API client
- **Env:** `NEXT_PUBLIC_API_BASE_URL`
- **Done 2026-11-12.** Next 16.4 / React 19.3 / Tailwind 4.3 / Vitest. Pages checked server-rendered against the live API; the search box itself is covered by component tests, a real-browser pass happens in M0-S7. Follow-ups: no paging on the episode list; malformed ids return FastAPI's 422 body; `#t=` audio fragments may be dropped by redirect hosts.

### [ ] M0-S4 — Transcribe once and store, with streamed progress
- **Owner:** backend-engineer · **Depends on:** M0-S1, M0-S2 · **Parallel with:** M0-S3 · **Contract:** migration `0002` (`transcripts` with `raw` JSONB and nullable `storage_key`, `segments` with `start_ms`, `end_ms`, `speaker`, `tsvector`, `jobs`)
- **Files:** `backend/ingest/` (`transcriber.py` interface, `feed_transcript.py`, `gemini_transcriber.py`, `segmenter.py`, `runner.py`, `locks.py`), `backend/api/routes/` (`ingest.py`, `jobs.py`), migration `0002_*`
- **Build:** `POST /episodes/{id}/ingest` returns a job id at once; `GET /jobs/{id}/events` streams SSE; source order is feed transcript tag (VTT, SRT, JSON) then Gemini on 15-minute chunks cut near silence, per-turn timestamps offset by chunk start, speaker names from the intro; temp audio in a temp directory deleted in a `finally`; Postgres advisory lock per episode; in-process runner behind `enqueue_ingest()` so Inngest can replace it in P2-S1
- **Acceptance**
  - [x] A new episode shows step events and ends with a stored transcript and timestamped segments; the API call itself returns in under 500 ms
  - [x] A second request for the same episode makes no download and no model call (process once, serve many)
  - [ ] Two simultaneous requests produce one transcription; no audio file remains afterwards; `transcripts` records source, model and cost
- **Tests:** transcript-tag parsers on fixtures; chunk offset arithmetic; lock contention; failure and retry state; Gemini mocked with a recorded response
- **Env:** `GEMINI_API_KEY`
- **Built 2026-11-13; slice stays open until one live Gemini transcription succeeds.** Verified live: feed-transcript path end to end (202 in 41 ms, 241 timestamped segments, replay after restart), idempotent second request, concurrent requests → one job; Gemini path live up to the model call (download through redirects, ffmpeg re-encode, temp audio removed, clean `failed` state on the rejected key). Third box is proven only with the fake provider (236 tests pass); model, cost and transcript quality on real audio are unverified because `GEMINI_API_KEY` is rejected. Notes: in-process runner assumes a single API process (P2-S1 replaces it); temp audio sits on local disk during a job (object storage in P1-S2). **2026-11-18:** the feed path was re-verified live (a 9.5-minute Buzzcast episode, 202 in 25 ms, 30 segments, idempotent repeat). The slice stays open until one live Gemini transcription succeeds with a valid key.

### [ ] M0-S5 — Ask-anything and three presets from stored text, with citations
- **Owner:** langgraph-engineer · **Depends on:** M0-S4 · **Contract pre-step (backend-engineer):** migration `0003` (`artifacts`, `requests` with nullable `user_id`); routes `POST /episodes/{id}/ask` (SSE) and `GET /episodes/{id}/artifacts/{kind}` wired to the graph entry points
- **Files:** `backend/agent/answer_graph.py`, `backend/agent/presets.py`, `backend/agent/prompts/` (versioned: `ask`, `tldr`, `takeaways`, `verdict`), `backend/agent/citations.py`, `backend/api/routes/` (`ask.py`, `artifacts.py`), migration `0003_*`
- **Build:** whole transcript in the prompt with prompt caching (no retrieval yet); structured claims carrying `segment_id`; validator drops claims without a valid segment and attaches `start_ms`; presets cached in `artifacts` by episode, kind and `prompt_version`; `verdict` = who it is for, what is new, best 10 minutes; `requests` row stores question, answer, citations, tokens, cost, model
- **Acceptance**
  - [ ] "Summarize for a startup founder" and "only the parts about X" give different, on-angle answers that stream (AC-ASK)
  - [ ] Every claim carries a timestamp citation; uncited claims are never returned
  - [ ] Second request for any preset makes zero model calls (AC-PRESET)
  - [ ] No audio is sent on this path; a repeat question on a 1-hour episode costs under $0.01 in `requests` (G1)
- **Tests:** citation validator (invalid id, missing id); preset cache hit, miss and version bump; graph run on a fixture transcript with a fake gateway; SSE event order
- **Env:** `GEMINI_API_KEY`
- **Code written 2026-11-18, awaiting user review** (user reviews `backend/agent/` and prompts personally; do not tick until approved). Tests added: `test_citations.py`, `test_answer_graph.py`, `test_presets.py`, `test_answer_routes.py`, `test_answer_cost.py` (fake provider, docker Postgres); the 2nd and 3rd boxes are covered by those tests, and the 4th by the cost arithmetic plus a text-only prompt assertion. The 1st box (on-angle answers) and real costs in `requests` are **not verified live**: `GEMINI_API_KEY` is still rejected (`API_KEY_INVALID`, re-checked 2026-11-18). Live demo: ask/preset on a stored episode fail cleanly with `llm_auth` (503 / SSE `error` then `done`). Prompt versions: `system-v1`, `ask-v1`, `tldr-v1`, `takeaways-v1`, `verdict-v1` (stored as `<task>-vN+system-v1`). Follow-ups: `verdict-v1` asks for exactly two citations on `best_10_minutes` but the validator only caps at 3 (enforce or resolve in P3-S5); a question with nothing related in the episode ends as `no_grounded_claims` (error), which may deserve a friendlier "not covered" answer; `_sse_frames` is imported privately from `ask.py` by `chat.py` (move to `routes/common.py`); `ErrorResponse.code` still lists `not_implemented` and the artifacts 502 description says "iTunes or feed host" (fix with the next schema change, P3-S1).

### [ ] M0-S6 — Simple episode chat on the Postgres checkpointer
- **Owner:** langgraph-engineer · **Depends on:** M0-S5 · **Parallel with:** M0-S7 · **Contract:** none (route shape fixed in schemas v0; route wiring by backend-engineer)
- **Files:** `backend/agent/chat_graph.py`, `backend/agent/checkpointer.py`, `backend/api/routes/chat.py`
- **Build:** `langgraph-checkpoint-postgres`; thread id `ep:{episode_id}` (becomes `u:{user_id}:ep:{episode_id}` in P3-S6); one graph run per message instead of the old interrupt loop; reuses the citation validator; `POST /episodes/{id}/chat` (SSE), `GET /episodes/{id}/chat` (history)
- **Acceptance**
  - [ ] Follow-up questions are answered with citations from the stored transcript and survive an API restart (AC-CHAT)
  - [ ] No `SqliteSaver`, `checkpoints.db` or Gemini file URI anywhere under `backend/`
- **Tests:** multi-turn persistence against docker Postgres; chat on an episode whose audio URL now returns 404
- **Env:** `GEMINI_API_KEY`
- **Code written 2026-11-18, awaiting user review** (do not tick until the user approves). `agent/chat_graph.py`, `agent/checkpointer.py` (`AsyncPostgresSaver`, pool over `settings.database_url_libpq`, thread id via `thread_id_for()`), `agent/prompts/chat.txt` (`chat-v1`); routes wired in `api/routes/chat.py`, saver opened in the `api/main.py` lifespan; Alembic `include_name` filter ignores LangGraph's 4 tables (`alembic check` clean). Tests: `test_chat_graph.py` (13, including restart persistence, 404 audio with zero HTTP calls, and an absence grep for SqliteSaver/file URIs) plus chat route tests in `test_routes.py`; 294 backend tests pass in both forward and reverse file order. Review fix: test modules that imported `litellm` directly loaded `.env` into `os.environ` (order-dependent); there is now a ruff `TID251` ban on `litellm` outside `llm/**` and a subprocess test, `tests/test_env_isolation.py`. The 2nd box holds (grep clean). The 1st box passes with the fake provider only; live is blocked on the Gemini key. Design notes: failed turns are not stored (history always alternates user/assistant); the model sees at most 8 prior messages; turn serialization is per process only (fine until P2-S1).

### [ ] M0-S7 — Web episode page: progress, ask, presets, citations, chat; prototype demo
- **Owner:** frontend-engineer (orchestrator runs the demo check) · **Depends on:** M0-S3, M0-S5; chat panel after M0-S6
- **Files:** `frontend/app/episode/[id]/` (job progress, verdict, preset tabs, ask box, answer stream, citation link, chat panel), `frontend/lib/sse.ts`, `README.md` quickstart (orchestrator)
- **Acceptance**
  - [x] Opening an unprocessed episode starts ingest and shows live progress that survives a refresh
  - [ ] Worth-it verdict shows first, then TL;DR and takeaways tabs, one click each (AC-WORTH, AC-PRESET)
  - [ ] The ask box streams a cited answer; each citation opens the episode audio at that time in a new tab
  - [ ] Chat panel streams follow-ups and reloads its history
  - [ ] **Prototype demo:** from a fresh clone with only `.env`, three shows from different genres are searched and one episode each summarized, asked and chatted with; LangSmith shows the traces
- **Tests:** SSE client reconnect; citation link builder; one Playwright flow on a seeded episode
- **Env:** `NEXT_PUBLIC_API_BASE_URL`
- **Built 2026-11-18; boxes 2–4 verified against a mocked API only.** `npm run lint`, `typecheck`, `test` (80 vitest tests, `tests/workspace.test.tsx` covers verdict-first order, one fetch per tab, incremental claims, citation `href`/`target`, ingest reattach, chat history/stream, and the 501/409/502/503 states) and `npm run e2e` (1 Playwright flow against `e2e/mock-api.mjs`) all pass. Refresh reattach: the episode page reads `active_job_id` and replays `GET /jobs/{id}/events` from seq 1, falling back to the idempotent `POST /ingest`. Live demo 2026-11-18 (orchestrator): searches for Buzzcast (Tech News), Podnews Weekly Review (News Commentary) and Radiolab (Natural Sciences) took 0.31–0.37 s; episode lists took 0.8–1.5 s; a 9.5-minute Buzzcast episode ingested from its feed transcript (202 in 25 ms, 30 segments, repeat request returned the same finished job); the episode page server-rendered. Presets, ask and chat returned a clean `llm_auth` because `GEMINI_API_KEY` is rejected, so the demo box and the live halves of boxes 2–4 wait for a valid key. LangSmith traces not checked live. README quickstart added. Also fixed: `frontend/.gitignore` ignored `frontend/.env.example` (now un-ignored). Follow-up: citation labels are `m:ss` / `h:mm:ss` (not zero-padded `mm:ss`).

## 1. Recommended defaults where the PRD gives options

| Choice | Recommendation | One-line reason |
|---|---|---|
| Inngest vs Temporal | **Inngest** | Functions run inside the FastAPI app and the local dev server needs no account; Temporal needs separate workers and a paid cloud namespace |
| Neon vs Supabase | **Neon** | Plain Postgres with branch-per-migration; Supabase's bundled auth and storage would duplicate Clerk and R2 |
| Clerk vs Supabase Auth | **Clerk** | Prebuilt Next.js components and organization accounts (needed for creators in Phase 5) with the least custom code |
| Railway vs Fly | **Railway** | API and worker deploy from one repo with shared variables, and a Railway MCP/skill is already available to the agents |
| LiteLLM self-hosted vs hosted gateway | **LiteLLM as an in-process library** (no proxy server) through launch | One interface, fallbacks and cost tracking with nothing extra to deploy; move to the proxy when per-key spend caps are needed |
| LangSmith vs Langfuse | **LangSmith** | Already keyed and traced in the code |
| Celery fallback | Not used | The prototype uses an in-process runner behind `enqueue_ingest()`, replaced by Inngest in P2-S1 |
| Embeddings | **Gemini Embedding 2 (hosted) through launch**, EmbeddingGemma 2 revisited in Phase 4 | Uses the existing key and no model server; the bake-off in P2-S4 can overturn this (see decision D12) |
| Object storage in dev | **MinIO in docker-compose** (from P1-S2) | Same S3 API as R2, so storage code is final from Phase 1 and R2 is only needed at deploy |

## 2. Phase 1 — Complete the product core

Goal: finish what the PRD's Phase 1 asks for on top of the prototype. Gate **G1**.
Accounts: none new (Podcast Index keys optional). Local docker gains Redis and MinIO.

**Order and parallelism**

| Wave | Slices | Notes |
|---|---|---|
| 1 | P1-S1 ∥ P1-S2 ∥ P1-S3 ∥ P1-S4 | No migrations; S1 touches prompts and web, S2 ingest and infra, S3 reports, S4 discovery |
| 2 | P1-S5 | Cleanup and gate |

### [ ] P1-S1 — Remaining preset views
- **Owner:** langgraph-engineer, then frontend-engineer · **Depends on:** M0 · **Parallel with:** P1-S2, P1-S3, P1-S4 · **Contract:** `schemas.py` artifact kind enum extended
- **Files:** `backend/agent/prompts/presets/` (`action_items`, `quotes`, `breakdown`, `explain_new`), `backend/agent/presets.py`, `frontend/app/episode/[id]/` preset tabs
- **Build:** `breakdown` ports today's template from `graph_engine.py`
- **Acceptance**
  - [ ] Six presets plus the verdict, one click each, cached per episode (AC-PRESET)
  - [ ] Bumping a `prompt_version` regenerates only that kind
- **Tests:** each preset returns cited claims; version bump
- **Env:** `GEMINI_API_KEY`

### [ ] P1-S2 — Redis lock and cache, object storage for raw transcripts
- **Owner:** env-config-manager (compose, settings), then backend-engineer · **Depends on:** M0 · **Parallel with:** P1-S1, P1-S3, P1-S4
- **Files:** `infra/docker-compose.yml` (Redis, MinIO), `backend/core/settings.py`, `.env.example`, `backend/ingest/locks.py`, `backend/ingest/storage.py`, `backend/discovery/cache.py`, `backend/scripts/move_raw_to_storage.py`
- **Build:** Redis lock `lock:ingest:{episode_id}` replaces the advisory lock; search results cached in Redis; raw transcript moves from the `raw` column to S3-compatible storage under `storage_key` (MinIO locally, R2 at deploy)
- **Acceptance**
  - [ ] Warm search returns in under 1 s (AC-SEARCH)
  - [ ] Lock test passes with two API processes; existing transcripts are moved by one command
  - [ ] API and worker hold no local files after a job
- **Tests:** lock contention across processes; storage round trip; cache expiry
- **Env:** `REDIS_URL`, `S3_ENDPOINT_URL`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_BUCKET` (local defaults)

### [ ] P1-S3 — Cost accounting report
- **Owner:** observability-engineer · **Depends on:** M0 · **Parallel with:** P1-S1, P1-S2, P1-S4
- **Files:** `backend/evals/reports/cost_report.py`
- **Build:** cost per feature, first-time cost per episode, cost per repeat request and cache hit rate from `requests`, `transcripts` and `artifacts`; replaces `calculate_costs.py`
- **Acceptance**
  - [ ] Report prints the four figures for the episodes processed so far
- **Tests:** report on seeded rows
- **Env:** none

### [ ] P1-S4 — Discovery hardening
- **Owner:** backend-engineer · **Depends on:** M0 · **Parallel with:** P1-S1, P1-S2, P1-S3
- **Files:** `backend/discovery/` (`podcast_index.py`, `feed.py`, `ids.py`)
- **Build:** Podcast Index lookup when its keys exist (feed data, transcript tags); permanent-redirect handling; feeds without guids; very large feeds paged
- **Acceptance**
  - [ ] 20 shows across genres, including two long-running feeds with 500+ episodes, list correctly
  - [ ] Works with and without Podcast Index keys
- **Tests:** recorded fixtures for each edge case
- **Env:** optional `PODCASTINDEX_API_KEY`, `PODCASTINDEX_API_SECRET`

### [ ] P1-S5 — Remove legacy code and pass the gate
- **Owner:** backend-engineer (deletions), orchestrator (docs and gate) · **Depends on:** P1-S1 to P1-S4
- **Files:** delete `gpodder_utils.py`, `gemini_utils.py`, `graph_engine.py`, `main.py`, `run_agent.sh`, `calculate_costs.py`, `app.py`, root `pyproject.toml`, root `.python-version`; add `backend/Dockerfile`, `backend/scripts/gate_p1.py`, `.github/workflows/ci.yml` (ruff, pytest, npm lint, typecheck, test); update `README.md` and `CLAUDE.md`
- **Acceptance (G1)**
  - [ ] `grep -rn "/home/\|gPodder\|gemini-2.0" --include=*.py --include=*.toml --include=*.sh .` returns nothing
  - [ ] On a clean machine (fresh clone in a container, only `.env` supplied) the gate script searches 5 shows from different genres, summarizes one episode each, and exits 0
  - [ ] The gate script's repeat request on each 1-hour episode costs under $0.01
- **Tests:** gate script in CI with recorded fixtures; one live run recorded in this file with date and cost
- **Env:** `GEMINI_API_KEY`

## 3. Phase 2 — Pipeline and quality

Goal: word-level timestamps, hybrid retrieval, durable jobs, measured quality. Gate **G2**.
New accounts: **AssemblyAI** (required), **Anthropic** (required for the judge; Gemini 3.1 Pro is a temporary same-vendor fallback). Inngest runs on its local dev server. Optional: Podcast Index keys.

**Order and parallelism**

| Wave | Slices | Notes |
|---|---|---|
| 1 | P2-S1 ∥ P2-S2 ∥ P2-S6 | S2 owns migration 0004; S6 runs on Phase 1 transcripts |
| 2 | P2-S3 ∥ P2-S7 | S3 owns migration 0005 |
| 3 | P2-S4 ∥ P2-S5 ∥ P2-S9 | S4 owns migration 0006 and a models.yaml change |
| 4 | P2-S8 | Needs the final pipeline to ingest the 50 episodes |
| 5 | P2-S10 | Gate |

### [ ] P2-S1 — Durable ingest workflow with streamed progress
- **Owner:** backend-engineer (env-config-manager adds the Inngest dev server to docker-compose) · **Depends on:** Phase 1 · **Parallel with:** P2-S2, P2-S6 · **Contract:** `schemas.py` `JobEvent` gains step names for the new workflow
- **Files:** `backend/ingest/workflow.py` (replaces `runner.py`), `backend/api/main.py` (Inngest serve endpoint), `backend/api/routes/` (`ingest.py`, `jobs.py`), `infra/docker-compose.yml`
- **Build:** steps `resolve_source → acquire_transcript → segment → embed → extract (stub until Phase 4) → presets → done`, each idempotent and retried on its own; the M0 routes and SSE stream are unchanged for the front end; `jobs` table mirrors step state; pending question is answered as soon as the transcript lands, before embedding finishes
- **Acceptance**
  - [ ] Killing the worker mid-transcription and restarting resumes the same job without repeating finished steps
  - [ ] The API call returns in under 500 ms with a job id; progress streams step by step and resumes after a page refresh
  - [ ] Default presets exist in `artifacts` when the job ends (PRD request flow step 7)
- **Tests:** workflow with fake steps (retry, resume, idempotency); SSE contract test; lock plus workflow de-duplication
- **Env:** `INNGEST_DEV=1` locally (no account)

### [ ] P2-S2 — AssemblyAI transcription with word timestamps and speakers
- **Owner:** backend-engineer · **Depends on:** Phase 1 · **Parallel with:** P2-S1, P2-S6 · **Contract:** migration `0004` (`transcripts`: `audio_duration_ms`, `vendor_job_id`, `words_storage_key`)
- **Files:** `backend/ingest/assemblyai_transcriber.py`, `backend/ingest/routing.py`, migration `0004_*`
- **Build:** submit the public audio URL directly, fall back to download → bucket → presigned URL when the vendor cannot fetch it; poll with backoff (webhook added at deploy); word-level JSON to object storage; record cost and processed duration; routing: feed tag → AssemblyAI → `stt_cheap` by policy flag
- **Acceptance**
  - [ ] A 1-hour and a 3-hour episode transcribe end to end with diarization and word timings
  - [ ] Bucket holds no audio after the job; `transcripts` row has vendor, cost and duration
- **Tests:** recorded AssemblyAI responses (success, error, timeout); fallback path; cost calculation
- **Env:** `ASSEMBLYAI_API_KEY`

### [ ] P2-S3 — Speaker-aware segments and speaker naming
- **Owner:** backend-engineer (naming prompt from langgraph-engineer) · **Depends on:** P2-S2 · **Parallel with:** P2-S7 · **Contract:** migration `0005` (`segments`: `segmenter_version`, `speaker_confidence`)
- **Files:** `backend/ingest/segmenter.py` (v2), `backend/ingest/speakers.py`, `backend/agent/prompts/speaker_naming.py`, `backend/scripts/resegment.py`
- **Build:** segments of about 200–400 tokens with speaker turns kept whole; `start_ms`/`end_ms` from word timings; map diarization labels to names using the intro and episode metadata; low-confidence labels stored as "Speaker"
- **Acceptance**
  - [ ] 95% of segments fall in the 200–400 token band on 5 sample episodes; no segment splits a turn unless the turn exceeds the cap
  - [ ] Phase 1 transcripts re-segment with one command
- **Tests:** segmenter properties (boundaries monotonic, no gaps, token band); naming on fixtures including an unknown speaker
- **Env:** `GEMINI_API_KEY`

### [ ] P2-S4 — Embeddings, pgvector and hybrid retrieval
- **Owner:** langgraph-engineer · **Depends on:** P2-S3 · **Parallel with:** P2-S5, P2-S9 · **Contract pre-step (backend-engineer):** migration `0006` (`segments.embedding vector(768)`, `embedding_model`, HNSW index); `models.yaml` adds `embedding`
- **Files:** `backend/llm/embeddings.py`, `backend/agent/retrieval.py`, `backend/agent/tools.py`, `backend/evals/embedding_bakeoff.py`, `backend/scripts/reembed.py`
- **Build:** bake-off first on 200 real podcast questions (Gemini Embedding 2 vs EmbeddingGemma 2, recall@10 and MRR); hybrid search = vector + full-text fused by reciprocal rank; answer engine switches from whole-transcript prompts to retrieval above the 30,000-token threshold; `embedding_model` stored per row so re-embedding is a batch job. If EmbeddingGemma 2 is chosen, load it in float32 only (its model card says float16 silently degrades results)
- **Acceptance**
  - [ ] Bake-off report committed under `eval_reports/` and the winner recorded in section 7 (D12)
  - [ ] Hybrid search beats vector-only and keyword-only on the 200 questions, including exact-name queries
  - [ ] A 3-hour episode answers through retrieval with a repeat cost under $0.01
- **Tests:** retrieval against a seeded Postgres; fusion ranking; re-embed job idempotency
- **Env:** `GEMINI_API_KEY`; optional `HF_TOKEN` if the open weights are gated

### [ ] P2-S5 — Timestamp citations and the accuracy check
- **Owner:** langgraph-engineer (citations), observability-engineer (checker) · **Depends on:** P2-S3 · **Parallel with:** P2-S4 · **Contract:** `schemas.py` `Citation` gains `start_ms`, `end_ms`, `episode_id`, `quote`
- **Files:** `backend/agent/citations.py`, `backend/ingest/drift.py`, `backend/evals/timestamp_check.py`
- **Build:** citations resolve to the first cited word's time; link builder per audio host; drift guard compares stored processed duration with the current enclosure duration and falls back to nearest transcript text; checker samples 100 citations, re-transcribes a ±15 s window with a second model and fuzzy-matches the quote
- **Acceptance**
  - [ ] Checker reports ≥ 95% within 15 s on AssemblyAI-sourced episodes (AC-JUMP, G2)
  - [ ] Checker result reported separately for feed-tag and `stt_cheap` transcripts
- **Tests:** citation resolution on word fixtures; drift fallback; checker on a synthetic offset
- **Env:** `GEMINI_API_KEY`, `ASSEMBLYAI_API_KEY`

### [ ] P2-S6 — DeepEval setup and summarization metric (ISSUE-004)
- **Owner:** observability-engineer · **Depends on:** Phase 1 · **Parallel with:** P2-S1, P2-S2 · **Contract:** `models.yaml` adds `judge`
- **Files:** `backend/evals/` (`conftest.py`, `judge.py`, `test_summarization.py`), `backend/pyproject.toml` eval dependency group
- **Build:** judge model wired through the gateway (Claude Opus tier; Gemini 3.1 Pro only as a flagged fallback); `SummarizationMetric` over stored transcript + artifact pairs
- **Acceptance**
  - [ ] `uv run pytest backend/evals -k summarization` scores one stored episode and writes the score to LangSmith
- **Tests:** judge adapter with a fake model; metric run on a fixture pair
- **Env:** `ANTHROPIC_API_KEY` (or fallback), `GEMINI_API_KEY`

### [ ] P2-S7 — Faithfulness and hallucination metrics (ISSUE-005)
- **Owner:** observability-engineer · **Depends on:** P2-S6 · **Parallel with:** P2-S3
- **Files:** `backend/evals/test_faithfulness.py`, `backend/evals/thresholds.py`
- **Acceptance**
  - [ ] Faithfulness and hallucination metrics run per artifact kind and for ask-anything answers, with threshold 0.85
  - [ ] A deliberately fabricated summary fails the test
- **Tests:** positive and negative fixtures
- **Env:** `ANTHROPIC_API_KEY`

### [ ] P2-S8 — 50-episode regression set, batch report and CI (ISSUE-006)
- **Owner:** observability-engineer · **Depends on:** P2-S1, P2-S3, P2-S7
- **Files:** `backend/evals/datasets/regression_50.yaml`, `backend/evals/run_batch_eval.py`, `eval_reports/` (gitignored output), `.github/workflows/evals.yml` (manual and on prompt or model change)
- **Build:** 50 episodes across at least 8 genres, including accented speech, crosstalk, a long episode (3 h+) and episodes with feed transcripts; judge calls through the batch API; CSV and JSON report plus a LangSmith dataset
- **Acceptance**
  - [ ] One command ingests the set, scores it and writes a report with per-genre and per-kind scores
  - [ ] Episode list approved by the user (D16); ingest spend recorded (about $10.50 of transcription at the PRD's list price if episodes average one hour)
- **Tests:** report writer; dataset loader; a 3-episode smoke suite in CI
- **Env:** `ASSEMBLYAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`

### [ ] P2-S9 — Transcript quality check and default routing
- **Owner:** observability-engineer · **Depends on:** P2-S2 · **Parallel with:** P2-S4, P2-S5
- **Files:** `backend/evals/wer_check.py`, `backend/ingest/routing.py` (policy values only, applied by backend-engineer)
- **Build:** word error rate on 20 episodes per vendor (AssemblyAI, `stt_cheap`) using publisher transcripts as reference, including accented speech and crosstalk
- **Acceptance**
  - [ ] Report committed; default and free-tier routing policy recorded in section 7 (D13)
- **Tests:** WER arithmetic on fixtures
- **Env:** `ASSEMBLYAI_API_KEY`, `GEMINI_API_KEY`

### [ ] P2-S10 — Phase 2 gate
- **Owner:** orchestrator · **Depends on:** all Phase 2 slices
- **Acceptance (G2)**
  - [ ] Faithfulness ≥ 0.85 on the 50-episode suite (report linked here)
  - [ ] ≥ 95% of sampled timestamps within 15 s (report linked here)
  - [ ] Prompt or routing fixes made to reach the gate are re-run on the full suite

## 4. Phase 3 — Web product and launch

Goal: hosted Next.js + FastAPI product with accounts, quotas, billing, verdict, history and opt-out. Gate **G3**.
New accounts: Clerk, Stripe, Neon, Upstash, Cloudflare R2, Railway, Vercel, Inngest Cloud, Sentry, PostHog, a domain. Legal review must be booked at the start of the phase.

**Order and parallelism**

| Wave | Slices | Notes |
|---|---|---|
| 1 | P3-S1 | Contract freeze; blocks the front end |
| 2 | P3-S2 ∥ P3-S3 ∥ P3-S5 (backend part) | Deploy skeleton, first screen and verdict generation are independent |
| 3 | P3-S4 → P3-S5 (UI part) | Same page, same agent, in order; both extend the prototype's pages |
| 4 | P3-S6 | Migration 0007 |
| 5 | P3-S7 → P3-S8 | Migration 0008 in S8; S7 has none |
| 6 | P3-S9 ∥ P3-S10 ∥ P3-S11 | Migrations 0009 (S9) then 0010 (S10) merge in that order |
| 7 | P3-S12 | Hardening, beta, gate |

### [ ] P3-S1 — API contract v1 and typed client
- **Owner:** backend-engineer · **Depends on:** Phase 2 · **Contract:** `schemas.py` v1 (extends the prototype's v0), regenerated `frontend/lib/types.ts`
- **Files:** `backend/api/schemas.py`, `backend/api/routes/*` (existing routes versioned; `me`, `history`, `feedback`, `billing` added as stubs), `frontend/lib/types.ts`
- **Build:** freeze the launch API: error model, pagination, auth header placeholder, SSE event types named and versioned; types generated from OpenAPI, never hand-written
- **Acceptance**
  - [ ] Every route returns schema-valid responses against local data; CI fails if `types.ts` is stale
  - [ ] Token streaming for `ask` and `chat` delivers the first event in under 5 s on a stored episode (AC-ASK)
- **Tests:** contract tests per route; SSE event order; OpenAPI snapshot
- **Env:** none new

### [ ] P3-S2 — Staging deploy skeleton
- **Owner:** env-config-manager · **Depends on:** P3-S1 · **Parallel with:** P3-S3, P3-S5
- **Files:** `backend/Dockerfile` (with `ffmpeg`), `infra/railway/` config, `frontend/.env.example`, `.env.example`, `.github/workflows/deploy.yml`, `infra/README.md` (runbook)
- **Build:** Neon (prod + staging branch), Upstash, R2 bucket with a lifecycle rule deleting temp audio, Railway services `api` and `worker`, Inngest Cloud app, Vercel project; `alembic upgrade head` on deploy; CORS limited to the web origin; AssemblyAI webhook enabled
- **Acceptance**
  - [ ] Staging URL serves `/healthz` and a real search; a new-episode ingest completes on staging
  - [ ] No secret in the repo; every variable documented in `.env.example` with phase and source
- **Tests:** post-deploy smoke script (health, search, ingest of one short episode)
- **Env:** `DATABASE_URL`, `REDIS_URL`, `S3_*` (R2), `INNGEST_EVENT_KEY`, `INNGEST_SIGNING_KEY`, `NEXT_PUBLIC_API_BASE_URL`, `RAILWAY_TOKEN` (CI), Vercel project link

### [ ] P3-S3 — Web production pass: search and show pages
- **Owner:** frontend-engineer · **Depends on:** P3-S1 · **Parallel with:** P3-S2, P3-S5
- **Files:** `frontend/app/page.tsx`, `frontend/app/podcast/[id]/page.tsx`, `frontend/app/layout.tsx`, `frontend/components/`
- **Build:** the prototype pages brought to launch quality: server components by default, loading, empty and error states, search as you type, page metadata, design pass
- **Acceptance**
  - [ ] Search shows results in under 1 s warm against staging (AC-SEARCH)
  - [ ] Works at 360 px wide and by keyboard only; contrast and labels pass an accessibility check
- **Tests:** component tests; Playwright flow (search → show → episode)
- **Env:** `NEXT_PUBLIC_API_BASE_URL`

### [ ] P3-S4 — Web production pass: episode page
- **Owner:** frontend-engineer · **Depends on:** P3-S3
- **Files:** `frontend/app/episode/[id]/`, `frontend/lib/sse.ts`
- **Build:** all preset tabs, ask box, citations and job progress hardened: reconnect and resume, quota and error responses, per-host timestamp link formats, link back to the show's own page
- **Acceptance**
  - [ ] Free-text request streams a cited answer within 5 s on a stored episode; each citation opens the audio at `start_ms` (AC-ASK, AC-JUMP)
  - [ ] Presets are one click each and instant when cached (AC-PRESET)
  - [ ] A new episode shows progress that survives a refresh and a dropped connection, then answers the pending request
- **Tests:** SSE reconnect; citation link builder per host; Playwright flow on a seeded episode
- **Env:** `NEXT_PUBLIC_API_BASE_URL`

### [ ] P3-S5 — "Is this worth my time?" verdict
- **Owner:** langgraph-engineer (generation), frontend-engineer (display) · **Depends on:** P3-S1 (backend part), P3-S4 (UI part)
- **Files:** `backend/agent/prompts/presets/verdict.py`, `backend/agent/presets.py`, `backend/ingest/workflow.py` (verdict added to the presets step, applied by backend-engineer), `frontend/app/episode/[id]/verdict.tsx`
- **Build:** the prototype's `verdict` preset upgraded: generated at ingest rather than on first view, and "best 10 minutes" resolved to a cited time range from word timestamps
- **Acceptance**
  - [ ] Verdict renders above the summary on every processed episode and reads in about 30 seconds (AC-WORTH)
  - [ ] "Best 10 minutes" is a working timestamp range link
  - [ ] Verdict kind added to the regression suite and passes 0.85 faithfulness
- **Tests:** structured output schema; UI order test
- **Env:** none new

### [ ] P3-S6 — Accounts and history
- **Owner:** backend-engineer, then frontend-engineer · **Depends on:** P3-S4 · **Contract:** migration `0007` (`users`; `requests.user_id` foreign key); `schemas.py` adds `Me`, `HistoryItem`
- **Files:** `backend/api/auth.py` (Clerk JWT verification), `backend/api/routes/me.py`, `backend/api/routes/webhooks_clerk.py`, migration `0007_*`, `frontend/middleware.ts`, `frontend/app/(auth)/`, `frontend/app/history/page.tsx`
- **Build:** sign-in with social login; user row created by webhook; chat thread id becomes `u:{user_id}:ep:{episode_id}`; anonymous visitors can read cached presets, signed-in users can ask and trigger new episodes (D17)
- **Acceptance**
  - [ ] History lists past requests with their answers and persists across devices (AC-ACCT)
  - [ ] Requests without a valid token are rejected on protected routes; one user cannot read another's history or chat
- **Tests:** auth middleware (valid, expired, wrong audience); authorization tests; webhook signature
- **Env:** `CLERK_SECRET_KEY`, `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`, `CLERK_WEBHOOK_SIGNING_SECRET`

### [ ] P3-S7 — Quotas
- **Owner:** backend-engineer, then frontend-engineer · **Depends on:** P3-S6
- **Files:** `backend/api/quotas.py`, `backend/core/plans.py`, `frontend/components/usage-meter.tsx`
- **Build:** Redis monthly counters per user for new episodes and questions; plan limits in one config (Free: 3 new episodes, 20 questions, unlimited presets on processed episodes); checked before enqueue and before each ask; per-IP rate limit for anonymous traffic
- **Acceptance**
  - [ ] The 4th new episode and the 21st question on Free return a clear upgrade response; presets on processed episodes stay unlimited (AC-ACCT)
  - [ ] Counters are atomic under concurrent requests and reset monthly
- **Tests:** limit boundaries; concurrency; month rollover
- **Env:** `REDIS_URL`

### [ ] P3-S8 — Stripe subscriptions
- **Owner:** backend-engineer, then frontend-engineer · **Depends on:** P3-S7 · **Contract:** migration `0008` (`users`: `stripe_customer_id`, `subscription_status`, `plan_renews_at`); `schemas.py` adds billing types
- **Files:** `backend/api/routes/billing.py`, `backend/api/routes/webhooks_stripe.py`, migration `0008_*`, `frontend/app/pricing/page.tsx`, `frontend/app/account/page.tsx`
- **Build:** Stripe Checkout and customer portal; webhooks set `users.plan`; launch plans per D18; no usage-metered overage at launch
- **Acceptance**
  - [ ] In Stripe test mode: upgrade raises the quota within seconds, cancel returns the user to Free at period end, failed payment downgrades
  - [ ] Webhook handler is idempotent and signature-verified
- **Tests:** recorded webhook events (checkout completed, subscription updated, deleted, payment failed); replayed event
- **Env:** `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_PLUS` (and `STRIPE_PRICE_PRO` if sold at launch)

### [ ] P3-S9 — Per-user chat and answer feedback
- **Owner:** frontend-engineer, backend-engineer · **Depends on:** P3-S6 · **Parallel with:** P3-S10, P3-S11 · **Contract:** migration `0009` (`feedback`); `schemas.py` adds `Feedback`
- **Files:** `frontend/app/episode/[id]/chat.tsx`, `backend/api/routes/feedback.py`, migration `0009_*`, `backend/evals/datasets/from_feedback.py` (observability-engineer)
- **Acceptance**
  - [ ] Follow-up chat streams cited answers per user and episode and persists (AC-CHAT)
  - [ ] Thumbs up or down and "this citation is wrong" are stored; negative items are exportable into the regression set
- **Tests:** chat flow in Playwright; feedback API; export script
- **Env:** none new

### [ ] P3-S10 — Opt-out, legal pages and privacy controls
- **Owner:** backend-engineer, frontend-engineer · **Depends on:** P3-S6 · **Parallel with:** P3-S9, P3-S11 · **Contract:** migration `0010` (`podcast_optouts`, `optout_requests`)
- **Files:** `backend/api/routes/optout.py`, `backend/scripts/purge_podcast.py`, `backend/api/routes/me.py` (export, delete), `backend/discovery/` and `backend/ingest/` blocklist checks, `frontend/app/(legal)/` (terms, privacy, opt-out form), `docs/runbooks/optout.md`
- **Acceptance**
  - [ ] An opted-out feed disappears from search and cannot be ingested; purge removes its transcripts, segments, artifacts and stored files; runbook meets the 48-hour limit
  - [ ] A user can export and delete their account data from settings
  - [ ] Every summary page links to the show's own page or feed; no page exposes a full transcript
  - [ ] Legal review of terms, privacy policy and the opt-out process is complete (user action, G3)
- **Tests:** blocklist enforcement in discovery and ingest; purge completeness; account deletion
- **Env:** none new (`RESEND_API_KEY` optional for opt-out confirmations)

### [ ] P3-S11 — Production observability and online quality sampling
- **Owner:** observability-engineer · **Depends on:** P3-S2 · **Parallel with:** P3-S9, P3-S10
- **Files:** `backend/core/tracing.py`, `backend/evals/online_sampling.py`, `backend/evals/reports/weekly.py`, `frontend/instrumentation.ts`, `frontend/lib/analytics.ts`
- **Build:** Sentry on API, worker and front end; PostHog events `search`, `summary_requested`, `summary_served_cached`, `signup`, `upgrade`; 5% of answers scored by the judge in batch; weekly scheduled run of the timestamp check and of cost per feature, cache hit rate and new-episode cost per paying user; opaque user ids only in traces
- **Acceptance**
  - [ ] A forced error appears in Sentry from each service; the five events appear in PostHog
  - [ ] Weekly report shows time to first summary (cached and new), cache hit rate and cost per request
- **Tests:** sampling rate; event payloads contain no email or name
- **Env:** `SENTRY_DSN`, `NEXT_PUBLIC_SENTRY_DSN`, `NEXT_PUBLIC_POSTHOG_KEY`, `NEXT_PUBLIC_POSTHOG_HOST`, `ANTHROPIC_API_KEY`, `LANGSMITH_*`

### [ ] P3-S12 — Hardening, beta and launch gate
- **Owner:** orchestrator, with backend-engineer and env-config-manager · **Depends on:** all Phase 3 slices
- **Files:** `backend/scripts/load_test.py`, `backend/core/limits.py`, `infra/README.md`
- **Build:** production deploy on the final domain; provider spend caps and alerts; maximum episode length; invite flow for beta; Stripe switched to live keys only after legal review
- **Acceptance (G3)**
  - [ ] First summary for a new 1-hour episode in under 3 minutes at p50 on production, 20 episodes measured
  - [ ] First summary on a cached episode in under 10 seconds
  - [ ] 100 beta users signed up
  - [ ] Legal review complete; regression suite still ≥ 0.85 on the production prompts and models
- **Tests:** load test of 50 concurrent asks on cached episodes and 10 concurrent new-episode ingests
- **Env:** production values for every variable in section 8

## 5. Phases 4–5 outline

**Phase 4 — Personalization and cross-episode intelligence (gate: week-4 retention 25%; cross-episode answers pass the citation check)**
New accounts: Resend, a reranker (Cohere or Voyage), Notion and Todoist developer apps. Model access: Claude Sonnet tier or Gemini 3.1 Pro for `cross_episode`.

| Slice | Title | Owner | Depends on | Contract |
|---|---|---|---|---|
| [ ] P4-S1 | Standing profile as answer lens, switchable per request | langgraph-engineer, frontend-engineer | P3 | `users.profile`, schemas |
| [ ] P4-S2 | Entity and resource extraction in the ingest workflow (batch) and backfill | langgraph-engineer, backend-engineer | P3 | migration (`entities`, `mentions`) |
| [ ] P4-S3 | Entity resolution (alias merge, cross-show dedupe) | backend-engineer | P4-S2 | migration |
| [ ] P4-S4 | Ask a whole show or all shows: hybrid search, filters, rerank, stronger model, Pro plan | langgraph-engineer | P4-S2 | models.yaml (`cross_episode`, `rerank`), schemas |
| [ ] P4-S5 | Follow a show, scheduled feed polling, batch ingestion | backend-engineer | P3 | migration (`follows`) |
| [ ] P4-S6 | Daily briefing by email and in app (within 6 hours of release) | backend-engineer, frontend-engineer | P4-S5, P4-S1 | schemas |
| [ ] P4-S7 | Guest tracker with alerts within 24 hours | backend-engineer | P4-S3, P4-S5 | follows on entities |
| [ ] P4-S8 | Running library (books, papers, tools ranked by mentions) | backend-engineer, frontend-engineer | P4-S3 | schemas |
| [ ] P4-S9 | Disagreement map (at least 2 cited sources) | langgraph-engineer, frontend-engineer | P4-S4 | schemas |
| [ ] P4-S10 | Skip what I know (overlap with the user's own history) | langgraph-engineer | P4-S1, P4-S4 | schemas |
| [ ] P4-S11 | Action items export to Notion and Todoist | backend-engineer | P3 | OAuth tokens table |
| [ ] P4-S12 | Cross-episode citation eval and gate | observability-engineer | P4-S4 | none |

Parallel tracks after P4-S2: (S3 → S7, S8), (S4 → S9, S10), (S5 → S6), S1 and S11 any time.

**Phase 5 — Retention, growth and creators (gate: free-to-paid 3%, gross margin 75%)**

| Slice | Title | Owner |
|---|---|---|
| [ ] P5-S1 | Flashcards and quizzes with spaced repetition by email (`cards`) | langgraph-engineer, backend-engineer, frontend-engineer |
| [ ] P5-S2 | Weekly recap email with one-click unsubscribe | backend-engineer |
| [ ] P5-S3 | Public summary pages and quote cards (short excerpts, server-rendered, indexable) | frontend-engineer |
| [ ] P5-S4 | Creator workspace: feed ownership verification, show notes, chapters, newsletter drafts, Creator plan | backend-engineer, langgraph-engineer, frontend-engineer |
| [ ] P5-S5 | Browser extension (Apple Podcasts, Spotify web, YouTube pages) | frontend-engineer |
| [ ] P5-S6 | Usage-metered overage in Stripe; margin dashboard | backend-engineer, observability-engineer |

## 6. Build risks

| Risk | Where it bites | Mitigation in the plan |
|---|---|---|
| Long-audio speech-to-text timeouts and 3 h+ episodes | M0-S4, P2-S2 | Chunked Gemini path; AssemblyAI as an async job with polling inside a retried workflow step; maximum episode length |
| Timestamp drift from dynamic ad insertion and chunk boundaries | M0-S4, P2-S5 | Cut chunks at silence; store processed duration; nearest-text fallback; checker reported per transcript source |
| Gemini chunked transcripts miss the 15 s target | P2-S5, P2-S9 | Gate measured on AssemblyAI; `stt_cheap` enabled for users only if it passes (D13) |
| Migration from `SqliteSaver` | M0-S6 | No data migration: old threads key on gPodder row ids and expired Gemini file URIs (D15) |
| Feed transcript tags without usable timestamps or with poor text | M0-S4, P2-S9 | Parser validates timings; fall back to speech-to-text when absent |
| Same show under several feed URLs gives duplicate episode ids | M0-S1, P1-S4 | URL normalization, follow permanent redirects, store directory ids on `podcasts` |
| New 1-hour episode over 3 minutes | P3-S12 | Submit audio URL directly, answer as soon as the transcript lands, embed and extract afterwards; measure from P2-S2 |
| Embedding model switch forces a full re-embed | P2-S4 | Bake-off before indexing; `embedding_model` per row; re-embed script |
| Alembic head conflicts from parallel slices | all | Revision numbers pre-assigned; one migration slice open at a time |
| Exact model ids and prices differ from the PRD | M0-S2 onward | Ids and prices only in `models.yaml`; confirm against vendor pages before each phase |
| Judge and generator from the same vendor if no Anthropic key | P2-S6 | Fallback is flagged in reports and cannot be used to pass G2 |
| Podcast hosts block or throttle server downloads | M0-S4, P2-S2 | Honest user agent, backoff, vendor-side fetch first |
| Paid plan has little to sell at launch | P3-S8 | Plus and Pro features mostly arrive in Phase 4 (D18) |
| `ffmpeg` missing on the dev machine | M0-S4 | Bundled binary from `imageio-ffmpeg`; system `ffmpeg` in the Dockerfile from P1-S5 |
| In-process prototype jobs die with the API process | M0-S4 | `jobs` row marks the failed step and the request can be retried; durable workflow in P2-S1 |
| Whole-transcript prompts on 3 h+ episodes exceed the 1-cent target | M0-S5 | Prompt caching now; retrieval above 30,000 tokens in P2-S4; G1 measured on 1-hour episodes |
| Prototype shortcuts leak into the product | M0 → P1 | Interfaces fixed in M0 (`Transcriber`, `enqueue_ingest()`, gateway, schemas v0) so later slices swap implementations without changing callers |
| Legal review is slower than the build | P3-S10, P3-S12 | Book it in the first week of Phase 3 |

## 7. Decisions needed from the user

Milestone 0 is not blocked by any of these; it proceeds on the recommended answers to D2, D10, D11, D15 and D22. D1, D6, D12–D14, D16 and D21 are needed before Phase 2; the rest before Phase 3.

| # | Decision | Blocks | Recommended answer |
|---|---|---|---|
| D1 | Languages at launch (PRD open question) | P2-S2, P2-S8 | English only; keep the `language` column and a multilingual embedding model so other languages can be added without re-work |
| D2 | YouTube captions as a transcript source (PRD open question) | M0-S4 routing | No for v1: terms risk, and video-only content is a non-goal |
| D3 | First paying customer: listener or creator (PRD open question) | P3-S8 | Listener; the creator workspace stays in Phase 5 |
| D4 | Keep the PodderSum name (PRD open question) | P3-S2 domain, P3-S10 legal pages | Keep it as the working name through beta; settle before buying the domain and going live in Stripe |
| D5 | User location and EU data residency (PRD open question) | P3-S2 provisioning | Put all stores in one region; choose an EU region unless the audience is expected to be mostly US, and promise no formal residency at launch |
| D6 | Workflow engine | P2-S1 | Inngest |
| D7 | Database host | P3-S2 | Neon |
| D8 | Auth provider | P3-S6 | Clerk |
| D9 | Worker and API hosting | P3-S2 | Railway |
| D10 | Model gateway form | M0-S2 | LiteLLM as a library inside the backend; no proxy until spend caps per key are needed |
| D11 | LLM tracing tool | M0-S2 | Stay on LangSmith |
| D12 | Embedding model | P2-S4 | Gemini Embedding 2 hosted through launch unless the bake-off shows EmbeddingGemma 2 clearly better; revisit self-hosting in Phase 4 |
| D13 | Default and free-tier transcription | P2-S9, P3-S7 | Feed transcript first, then AssemblyAI for everyone; switch free-tier requests to the Gemini low-cost path only if it passes the 15-second check |
| D14 | Evaluation judge | P2-S6 | Create an Anthropic API account in Phase 2 and use the Opus tier in batch; Gemini 3.1 Pro only as a stopgap |
| D15 | Legacy local data (`checkpoints.db`, `summaries/`, `transcriptions/`) | M0-S6, P1-S5 | Do not migrate; leave the files untouched on disk and gitignored |
| D16 | The 50 regression episodes and the spend to ingest them | P2-S8 | Agents propose a list across 8+ genres, you approve it; budget about $10.50 for transcription plus judge calls |
| D17 | What anonymous visitors can do | P3-S6, P3-S7 | Read cached presets and verdicts without signing in; sign in to ask a question or process a new episode |
| D18 | Plans sold at launch | P3-S8 | Free and Plus only, with Plus sold on quota (40 new episodes, more questions); add Pro when cross-episode search ships in Phase 4; no metered overage and no Creator plan yet |
| D19 | Gemini API billing tier | P3-S2 | Enable paid billing before real users arrive, so user content falls under paid-tier data terms (PRD privacy rule) |
| D20 | Legal reviewer and timing | P3-S10, P3-S12 | Book a lawyer in the first week of Phase 3 for terms, privacy policy, opt-out process and vendor data processing agreements |
| D21 | Budget caps per provider | P2-S8, P3-S12 | Set a monthly cap and alert on Gemini, AssemblyAI and Anthropic before Phase 2 starts |
| D22 | Streamlit harness | M0, P1-S5 | Drop it: the Next.js prototype is the only UI; `CLAUDE.md` is updated to match |

## 8. Accounts, API keys and model access needed

Prices in the PRD were read on 10 November 2026. Gemini figures came from Google's pricing page; speech-to-text and Claude figures came from third-party roundups. Reconfirm every price, free-tier limit and exact model id on the vendor's own page before budgeting and before each phase.

Legend: **R** required, **O** optional, **–** not used. **M0** is the prototype milestone; P1 is the rest of Phase 1.

| Service / model | Env var name(s) | M0 | P1 | P2 | P3 | P4–5 | Free tier? | Notes |
|---|---|---|---|---|---|---|---|---|
| Gemini API: 3.1 Flash-Lite (summaries, presets, chat, low-cost transcription, extraction) | `GEMINI_API_KEY` (exists) | R | R | R | R | R | Yes, rate-limited | Confirm the exact model id; move off `gemini-2.0-flash`; paid tier before real users (D19) |
| Gemini API: 3.1 Pro (cross-episode alternative, stopgap judge) | `GEMINI_API_KEY` | – | – | O | O | O | Verify | Same key; only if chosen over Claude Sonnet tier |
| Gemini API: Gemini Embedding 2 | `GEMINI_API_KEY` | – | – | R (bake-off; default per D12) | R if chosen | R if chosen | Verify | PRD lists $0.20 per 1M text tokens |
| EmbeddingGemma 2 (self-hosted) | `HF_TOKEN` only if weights are gated | – | – | O (bake-off) | O | O | Open weights; pay for compute | Never load in float16; needs RAM on the worker |
| LangSmith | `LANGSMITH_API_KEY`, `LANGSMITH_TRACING`, `LANGSMITH_PROJECT`, `LANGSMITH_ENDPOINT` (exist) | R | R | R (eval datasets) | R | R | Yes | Tracing is a no-op without keys |
| Local Postgres + pgvector, Redis, MinIO (docker) | `DATABASE_URL`, `REDIS_URL`, `S3_ENDPOINT_URL`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_BUCKET` | R (Postgres only) | R | R | R (dev) | R (dev) | Local | No account; Redis and MinIO join in P1-S2 |
| iTunes Search API | none | R | R | R | R | R | Yes, no key | Rate-limited; cache results; follow Apple's terms |
| Podcast Index API | `PODCASTINDEX_API_KEY`, `PODCASTINDEX_API_SECRET` | – | O | O | R | R | Yes | Feed data and transcript tags; attribution required |
| AssemblyAI (Universal-3.5 Pro, batch) | `ASSEMBLYAI_API_KEY` | – | – | R | R | R | Trial credit, verify | PRD price $0.21 per audio hour is third-party sourced |
| Anthropic Claude, Opus tier (evaluation judge, batch) | `ANTHROPIC_API_KEY` | – | – | R | R | R | No | PRD prices were for Opus 4.8; use the current Opus-tier id and confirm price |
| Anthropic Claude, Sonnet tier (cross-episode answers) | `ANTHROPIC_API_KEY` | – | – | – | O | R (or Gemini 3.1 Pro) | No | PRD prices were for Sonnet 5; confirm current id and price |
| Reranker (Cohere Rerank or Voyage rerank) | `COHERE_API_KEY` or `VOYAGE_API_KEY` | – | – | – | – | R for cross-episode | Trial, verify | PRD: skip at launch |
| Inngest | `INNGEST_DEV` (local); `INNGEST_EVENT_KEY`, `INNGEST_SIGNING_KEY` (cloud) | – | – | R (local dev server, no account) | R (cloud) | R | Yes, verify limits | D6 |
| Neon Postgres | `DATABASE_URL` | – | – | – | R | R | Yes | Enable pgvector; region per D5 |
| Upstash Redis | `REDIS_URL` | – | – | – | R | R | Yes | Quotas, locks, cache |
| Cloudflare R2 | `S3_ENDPOINT_URL`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_BUCKET` | – | – | – | R | R | Yes | Lifecycle rule deletes temp audio |
| Railway (API + worker) | `RAILWAY_TOKEN` (CI only) | – | – | – | R | R | Trial only, verify | D9 |
| Vercel (front end) | project link; `NEXT_PUBLIC_API_BASE_URL` | – | – | – | R | R | Hobby tier is for non-commercial use, verify | Paid plan likely needed once billing is live |
| Clerk | `CLERK_SECRET_KEY`, `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`, `CLERK_WEBHOOK_SIGNING_SECRET` | – | – | – | R | R | Yes | D8 |
| Stripe | `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_PLUS`, `STRIPE_PRICE_PRO` | – | – | – | R (test mode, then live) | R | No monthly fee; per-transaction | Live keys only after legal review |
| Sentry | `SENTRY_DSN`, `NEXT_PUBLIC_SENTRY_DSN` | – | – | – | R | R | Yes | API, worker, front end |
| PostHog | `NEXT_PUBLIC_POSTHOG_KEY`, `NEXT_PUBLIC_POSTHOG_HOST` | – | – | – | R | R | Yes | Region per D5 |
| Domain and DNS | none | – | – | – | R | R | No | After D4 |
| Resend | `RESEND_API_KEY` | – | – | – | O | R | Yes | Briefing, recap, alerts |
| Notion and Todoist developer apps | `NOTION_CLIENT_ID`, `NOTION_CLIENT_SECRET`, `TODOIST_CLIENT_ID`, `TODOIST_CLIENT_SECRET` | – | – | – | – | R (export) | Yes | P4-S11 |
| GitHub Actions | repository secrets mirroring the above | – | O | R | R | R | Yes | CI, evals, deploy |

## 9. Orchestrator checklist per slice

- [ ] Prerequisite slices ticked; required env vars present (names only, never values); blocking decision answered
- [ ] Brief sent to the owner: goal, files it may touch, contract, acceptance boxes, tests
- [ ] Contract pre-step merged before the consumer starts
- [ ] `uv run ruff check && uv run pytest` (and `npm run lint && npm run typecheck && npm test` for the front end) pass
- [ ] Diff reviewed against CLAUDE.md principles: no model id outside `models.yaml`, no local file state, no blocking on transcription, citations enforced
- [ ] `.env.example` updated for every new variable
- [ ] Slice and acceptance boxes ticked here; follow-ups noted under the slice
