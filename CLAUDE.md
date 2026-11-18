# CLAUDE.md — PodderSum

PodderSum is moving from a single-user Streamlit + gPodder app to a hosted, multi-user web product:
search any public podcast, pick an episode, ask for a summary from any angle, and get answers where
every claim links to a timestamp in the audio.

**Source of truth for product scope:** `docs/PRD-003-product.md` (the product PRD, Nov 2026).
Older PRDs/issues live in `issues/` (PRD-001 transcription, PRD-002 DeepEval, ISSUE-001..006).
**Implementation plan:** `plans/poddersum-implementation-plan.md`.

## Current state (legacy code, Phase 0)

| File | Role | Fate |
|---|---|---|
| `app.py` | Streamlit UI | Not carried forward; the Next.js app is the UI from Milestone 0. Deleted in P1-S5 |
| `graph_engine.py` | LangGraph agent (download → summarize → chat), `SqliteSaver` checkpoints | Refactored into `backend/agent/`; checkpointer → Postgres |
| `gemini_utils.py` | Audio download + Gemini file upload | Replaced by transcript-first pipeline |
| `gpodder_utils.py` | Reads local gPodder SQLite DB | **Deleted**; replaced by discovery module (iTunes Search + Podcast Index) |
| `main.py`, `run_agent.sh` | Cron batch summarizer | Replaced by durable background workflows |
| `calculate_costs.py` | Token cost report from checkpoints | Replaced by per-request cost in `requests` table + LangSmith |

Known debt to fix early: hardcoded `/home/aswinmanohar/...` paths, `pyproject.toml` has no
dependencies and wrong `requires-python` (PRD targets 3.12), model is `gemini-2.0-flash`
(outdated), audio is re-sent to Gemini for every chat question.

## Target architecture (from PRD)

```
frontend/   Next.js (App Router, TypeScript, Tailwind) — SSE streaming, deployed to Vercel
backend/    FastAPI (Python 3.12, Pydantic v2, async)
  api/        HTTP routes, SSE endpoints, auth middleware (Clerk JWT)
  discovery/  iTunes Search + Podcast Index, feed parsing, <podcast:transcript> tag
  ingest/     durable workflow: fetch transcript or audio → STT → segment → embed → extract entities
  agent/      LangGraph graphs (answer engine, episode chat, cross-episode) + tools
  llm/        model gateway (LiteLLM) + models.yaml (ALL model names live here, nowhere else)
  db/         SQLAlchemy models + Alembic migrations (Postgres + pgvector + FTS)
  evals/      DeepEval suites (50-episode regression set)
infra/      docker-compose for local Postgres/Redis, deploy config
```

Core principles (non-negotiable):
- **Process once, serve many.** Episode id = `sha256(feed_url + guid)`. Redis lock prevents duplicate transcription.
- **Everything slow is a job.** API returns a job id and streams progress; it never blocks on transcription.
- **Stateless services.** No local files; audio goes to R2 temporarily and is deleted after processing.
- **Grounded answers.** Prompts receive segments with ids + timestamps; every claim must cite a segment; uncited claims are dropped.
- **Model names only in `backend/llm/models.yaml`.** Never hardcode a model id in code.
- **Secrets only via env vars.** Never commit `.env`; keep `.env.example` in sync with every new variable.

## Commands

> Fill in / update as the scaffold lands.

```bash
# backend
cd backend && uv sync && uv run uvicorn api.main:app --reload --port 8010
uv run pytest
uv run alembic upgrade head
# frontend
cd frontend && npm install && npm run dev
# local infra
docker compose -f infra/docker-compose.yml up -d   # postgres (pgvector) on host port 5433; redis arrives in Phase 1
```

## Agent team (`.claude/agents/`)

The main session (or `orchestrator`, Fable 5.2) plans, splits work, and reviews. Implementation agents run on Opus.

| Agent | Owns |
|---|---|
| `orchestrator` | Reads plan, dispatches slices, reviews diffs against PRD acceptance criteria, keeps the plan's checklist current |
| `backend-engineer` | `backend/api`, `backend/db`, `backend/discovery`, `backend/ingest`, auth/quotas/billing |
| `frontend-engineer` | `frontend/` |
| `env-config-manager` | `.env.example`, settings module, secrets wiring, docker-compose, deploy env vars |
| `langgraph-engineer` | `backend/agent/` graphs, tools, checkpointer, retrieval, prompts, `backend/llm/` |
| `observability-engineer` | LangSmith tracing, cost/token accounting, Sentry, PostHog, DeepEval in `backend/evals/` |

Ownership rule: an agent edits only its own paths; cross-cutting changes (e.g. an API contract) are
proposed by the owner and applied by the orchestrator in sequence. Shared contracts live in
`backend/api/schemas.py` (Pydantic) and are mirrored to `frontend/lib/types.ts`.

## Conventions

- Python: type hints everywhere, `ruff` + `pytest`, async I/O in the API, `uv` for deps.
- TypeScript: strict mode, server components by default, no `any`.
- Commits: conventional (`feat:`, `fix:`, `chore:`), one vertical slice per PR.
- Tests: each slice ships with a test that exercises it end to end (mock vendors with recorded fixtures).
- Don't touch `downloads/`, `summaries/`, `transcriptions/`, `checkpoints.db` — local data, gitignored or to be gitignored.
