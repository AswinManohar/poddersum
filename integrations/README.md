# PodderSum integrations: MCP server + Agent Skills

Plug PodderSum into any agent host: ask "summarize this podcast for me: Hidden Brain latest
episode" and get a summary where every point links to its moment in the audio
(`[5:12](https://.../episode.mp3#t=312)`).

**No API key needed.** Nothing here needs a PodderSum key or a model-vendor key for local use.
The summary is written by one of two engines:

* **Host model.** PodderSum returns the episode's timestamped transcript and **your agent's own
  model** writes the cited summary: your Claude subscription in Claude Code, ChatGPT/Codex in
  Hermes or OpenClaw, Grok in Grok Build, and so on. Use it with `engine="host"`, or let it
  happen automatically whenever the backend has no working model.
* **Server model.** PodderSum's backend writes the summary and caches it once per episode,
  validated so that every claim cites a segment. Used when the backend has a model (soon via a
  local subscription CLI, with no key either).

`engine="auto"` (the default) uses the server model if it works and the host model otherwise,
so a single `summarize_episode` call always gets you a summary.

| Piece | Path | What it is |
|---|---|---|
| MCP server | `mcp/` | `poddersum-mcp`, a Python 3.12 package on the official `mcp` SDK (2.x `MCPServer`, formerly FastMCP). A thin httpx client over the PodderSum HTTP API: no DB access and no model calls of its own. stdio by default; Streamable HTTP with `--transport http`. |
| Skills | `skills/poddersum-summarize/`, `skills/poddersum-ask/` | Open Agent Skills format (`SKILL.md` + `scripts/poddersum.py`). The script is stdlib-only (Python 3.9+), so the skills also work on hosts without MCP. |
| Claude Code plugin | `claude-plugin/` (sources) -> `dist/claude-plugin/` | Manifest + `.mcp.json`; `build.sh` adds the skills and the server source. |
| Host snippets | `hosts/<host>/` | Config files for Hermes, OpenClaw, Claude Desktop, Grok, Muse. |
| Tests | `tests/` | Fake API with recorded contract-v0 responses, MCP over stdio + HTTP, CLI script, live e2e, agent skill-trigger e2e. |

Everything talks to the PodderSum API. Start it first (see the repo `CLAUDE.md`):

```bash
docker compose -f infra/docker-compose.yml up -d
cd backend && uv run uvicorn api.main:app --port 8010
```

## Build

```bash
integrations/build.sh            # -> integrations/dist/
```

`dist/claude-plugin/` (+ `.zip`), `dist/skills/<skill>/` (+ one `.zip` per skill), and
`dist/mcp/*.whl`. The skills' `scripts/poddersum.py` has one source,
`skills/poddersum-summarize/scripts/poddersum.py`; the build copies it into every skill
(`build.sh --check` fails if they drift).

## Configuration (environment only)

| Variable | Default | Used by |
|---|---|---|
| `PODDERSUM_API_URL` | `http://localhost:8010` | MCP server, `poddersum.py` |
| `PODDERSUM_ENGINE` | `auto` | Default engine: `auto`, `host` (always let the agent's model write it), or `server`. |
| `PODDERSUM_API_KEY` | unset | **Not needed.** Reserved for a future hosted PodderSum (sent as `Authorization: Bearer ...` if set). |
| `PODDERSUM_WAIT_S` | `240` | Max seconds a call waits for transcription before returning `status=processing` (call again to resume the same job). Kept under Hermes' 300 s default tool timeout. Max 1800. |

## MCP tools

| Tool | Does |
|---|---|
| `search_podcasts(query, limit=5)` | Shows matching a name, with `podcast_id`. |
| `list_episodes(podcast_id, limit=10)` | Newest first, with `episode_id`, date, duration, transcript status. |
| `get_episode(episode_id)` | Details, audio URL, show notes, status. |
| `summarize_episode(episode_id \| podcast_name + episode_hint, style, angle?, engine?, wait_s?)` | The one-call happy path: resolve, ingest if needed (follows the job's SSE stream and sends MCP progress notifications), then either `status=ok` (server summary: the cached preset `tldr`/`takeaways`/`verdict`, or an angled ask) or `status=transcript` (segments + task + citation rules, for the host model to write). Returns `needs_choice` + candidates when the episode is ambiguous, `processing` + job id on timeout, `model_unavailable` + hint only with `engine="server"`. |
| `get_transcript(episode_id \| podcast_name + episode_hint, start_ms?, end_ms?, max_chars?)` | Timestamped segments `[segment_id] m:ss (t=seconds) Speaker: text` plus `audio_url`/`link_base`; ingests first if needed; paginated (`next_start_ms`). Needs the backend route `GET /episodes/{id}/transcript` (not in contract v0 yet, see API gaps). |
| `ask_episode(episode_id, question, engine?)` | One grounded question (stateless). Falls back to the transcript like summarize. |
| `chat_episode(episode_id, message, engine?)` | Episode chat with server-side history. Falls back to the transcript (no server history then). |
| `get_job_status(job_id)` | Ingest job state/step/progress. |
| prompt `summarize_podcast(podcast, episode="latest", angle="", style="tldr")` | Mirrors the skill workflow. |

Every tool returns compact markdown (timestamp links `[m:ss](audio_url#t=seconds)`, the same
format as the web app) plus the same data as `structuredContent` (citations also carry
`timestamp` and `url`). If the API is down, tools say
`PodderSum API not reachable at <url>; start it with ...`.

Run it by hand:

```bash
uv run --project integrations/mcp poddersum-mcp                               # stdio
uv run --project integrations/mcp poddersum-mcp --transport http --port 8765  # http://127.0.0.1:8765/mcp
uvx --from integrations/dist/mcp/poddersum_mcp-0.1.0-py3-none-any.whl poddersum-mcp
```

## Install per host

Replace `/path/to/poddersum` with your checkout. "Verified" means it was exercised on this
machine; see the bottom of this file for what each test actually ran.

### Claude Code (verified)

No API key needed: the host-model path uses your Claude subscription.

```bash
integrations/build.sh
claude --plugin-dir integrations/dist/claude-plugin          # this session only
```

The plugin registers the `poddersum` MCP server (launched with
`uv run --project ${CLAUDE_PLUGIN_ROOT}/server poddersum-mcp`, so `uv` must be on PATH) and
the skills `poddersum:poddersum-summarize` / `poddersum:poddersum-ask`. Tools show up as
`mcp__plugin_poddersum_poddersum__<tool>`. To install it permanently, publish
`dist/claude-plugin` in a marketplace repo (it is gitignored here, so a marketplace pointing at
this repo needs it committed or built in CI) and use `/plugin marketplace add` + `/plugin install`.
Skills only (no plugin): copy `dist/skills/*` to `~/.claude/skills/`.

### Claude Desktop (snippet; not run here: no Claude Desktop on this Linux box)

No API key needed.

Add `hosts/claude-desktop/claude_desktop_config.snippet.json` to `claude_desktop_config.json`
(macOS `~/Library/Application Support/Claude/`, Windows `%APPDATA%\Claude\`) and restart.
For the skills, upload `dist/skills/poddersum-summarize.zip` and `poddersum-ask.zip` under
Settings > Capabilities > Skills. Skills there run in Claude's code sandbox, which most likely
cannot reach your `localhost` API (not verified), so in Desktop rely on the MCP tools.

### Hermes Agent (verified, v0.20.5)

No API key needed for PodderSum; Hermes' own model (for example ChatGPT/Codex) writes the summary.

Merge `hosts/hermes/config.snippet.yaml` into `~/.hermes/config.yaml` (`mcp_servers` +
`skills.external_dirs`), then `/reload-mcp` or restart. Or:
`hermes mcp add poddersum -- uv run --quiet --project /path/to/poddersum/integrations/mcp poddersum-mcp`.
Check with `hermes mcp test poddersum` and `hermes skills list`.

### OpenClaw (verified, 2026.2.3-1, skills only)

No API key needed for PodderSum; OpenClaw's own model writes the summary from `poddersum.py transcript` output.

This OpenClaw build has no native MCP client (only the optional `mcporter` skill), so it uses
the skills' stdlib script. Merge `hosts/openclaw/openclaw.snippet.json5` into
`~/.openclaw/openclaw.json` (`skills.load.extraDirs`), or copy `dist/skills/*` into
`~/.openclaw/skills/` or `<workspace>/skills/`. Check with `openclaw skills list`. The
frontmatter keeps every key on one line, because OpenClaw's parser requires that. Note:
OpenClaw bundles a skill named `summarize` (it needs the `summarize` CLI). If you install that CLI,
both skills compete for "summarize this podcast"; ours says "Summarize a podcast episode
with PodderSum" so the agent can tell them apart.

### Grok Build (snippet from docs.x.ai; grok CLI not installed here)

No API key needed; Grok writes the summary through the host-model path.

`hosts/grok/config.snippet.toml` -> `~/.grok/config.toml`, or
`grok mcp add poddersum -- uv run --quiet --project /path/to/poddersum/integrations/mcp poddersum-mcp`.
Skills: copy `dist/skills/*` to `~/.grok/skills/` or `~/.agents/skills/`. Per xAI's docs,
Grok Build also reads Claude Code plugins, so `--plugin-dir dist/claude-plugin` should work as is.

### Grok Bot (remote-only; from docs.x.ai, not run here)

No API key needed for PodderSum.

Grok Bot connects to MCP servers by **Remote HTTPS** URL or by a **Command** on its own
computer (desktop app: Connect apps > Add a custom connector). Your local API is not reachable
from its cloud computer, so run both behind HTTPS:

```bash
poddersum-mcp --transport http --host 127.0.0.1 --port 8765 --allowed-host <your-tunnel-host>
# expose http://127.0.0.1:8765 with a tunnel (cloudflared, ngrok, tailscale funnel) and
# set PODDERSUM_API_URL to an API the MCP server can reach.
```

Then add the connector with Server URL `https://<your-tunnel-host>/mcp`. `--allowed-host` adds
the tunnel's Host header to the DNS-rebinding allow-list (with `--host 0.0.0.0` the check is
off). There is no auth on the MCP endpoint yet; only expose it on a private tunnel.
Grok Bot skills are saved from chat ("save this as a skill"); paste the body of
`skills/poddersum-summarize/SKILL.md` there (unverified that it keeps the frontmatter).

### Muse (UNVERIFIED)

No API key needed.

No Muse CLI or public docs were found; see `hosts/muse/README.md` for a best-effort snippet.

### Claude Duet (placeholder)

No public product named "Claude Duet" was found. Waiting for the user to say which host this is.

## Tests

```bash
cd integrations
uv run pytest                       # unit + contract + MCP (stdio and HTTP) + CLI; no network, no models
uv run pytest -m live               # real API at PODDERSUM_API_URL; skipped if it is down
uv run pytest -m agents -s -rA      # headless Claude Code / Hermes / OpenClaw runs (spends quota)
uv run ruff check . && uv run ruff format --check .
```

* The fixtures in `tests/fixtures/api/` are contract-v0 shaped and validated against
  `backend/api/schemas.py` by `test_contract_fixtures.py`, so backend contract drift fails here.
* Live: picks the shortest episode of `PODDERSUM_LIVE_SHOW` (default Buzzcast, which has
  feed transcripts, so no speech-to-text spend), checks ingest, then one TL;DR and one ask;
  `status=transcript` (no server model) passes. A missing transcript route skips with the
  API-gap reason. `test_live_get_transcript` covers the host-model path.
* Host model: `tests/test_host_model.py` covers `get_transcript` (formatting, pagination,
  ranges, ingest-first), auto fallback when the fake reports `llm_auth`/503, `engine=host`,
  `engine=server` hints, and the API-gap message. The fake API implements the proposed
  transcript route (`POST /_fake/config {"llm_down": true, "transcript_route": false}`).
* Agents: Claude Code runs a server-summary positive, a host-model fallback (server model
  down), "use my own model", and 2 negatives. Hermes and OpenClaw each run one positive with
  the server model down, so their own model writes the summary. A host-written summary must
  contain at least 2 links, and every `#t=` must be a real segment start (never invented).
  Every run talks to the fake API, and "did it use PodderSum" is read from the fake
  API's request log (User-Agent `poddersum-mcp/` or `poddersum-cli/`). No global config is
  touched: Claude Code runs in a temp cwd with `--setting-sources project,local`,
  `--plugin-dir`, `--no-session-persistence`, and a budget cap. Hermes uses a temp
  `HERMES_HOME`; OpenClaw uses temp `OPENCLAW_STATE_DIR`/`OPENCLAW_CONFIG_PATH`. The
  OpenRouter key is forwarded from `~/.hermes/.env` and never printed. Hosts and models:
  `PODDERSUM_AGENT_HOSTS`, `PODDERSUM_AGENT_MODEL_CLAUDE` (sonnet),
  `PODDERSUM_AGENT_MODEL_OPENROUTER` (deepseek/deepseek-chat), `PODDERSUM_AGENT_MODEL_OPENCLAW`.
  Negatives run on Claude Code only unless `PODDERSUM_AGENT_NEGATIVES=all`.
* `python tests/fake_api.py --port 8010` serves the fake API standalone for manual host tests.
