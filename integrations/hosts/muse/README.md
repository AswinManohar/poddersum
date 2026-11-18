# Muse (UNVERIFIED)

No API key needed.

No Muse CLI is installed on this machine and no public Muse documentation for skills or
MCP could be found, so nothing here is verified. If your Muse build reads the open Agent
Skills format and standard MCP configs:

* Skills: copy `integrations/dist/skills/poddersum-summarize` and `.../poddersum-ask` into
  Muse's skills directory.
* MCP (stdio): command `uv`, args
  `["run", "--quiet", "--project", "/path/to/poddersum/integrations/mcp", "poddersum-mcp"]`,
  env `PODDERSUM_API_URL=http://localhost:8010`.
* MCP (remote only): run `poddersum-mcp --transport http --port 8765` and point Muse at
  `http://<host>:8765/mcp` (see the Grok Bot section of integrations/README.md for tunnels).
