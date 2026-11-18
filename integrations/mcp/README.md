# poddersum-mcp

MCP server for PodderSum: search podcasts and get cited, timestamped episode summaries.
A thin client over the PodderSum HTTP API (contract v0); no database, no model calls.

```bash
uv run poddersum-mcp                                   # stdio
uv run poddersum-mcp --transport http --port 8765      # Streamable HTTP at /mcp
```

Environment: `PODDERSUM_API_URL` (default `http://localhost:8010`), `PODDERSUM_API_KEY`
(optional Bearer token), `PODDERSUM_WAIT_S` (default 240).

Tools: `search_podcasts`, `list_episodes`, `get_episode`, `summarize_episode`,
`ask_episode`, `chat_episode`, `get_job_status`; prompt `summarize_podcast`.
See `integrations/README.md` for host setup and tests.
