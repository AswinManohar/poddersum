# PodderSum Claude Code plugin (sources)

This folder holds the plugin manifest (`.claude-plugin/plugin.json`) and MCP config
(`.mcp.json`) only. `integrations/build.sh` assembles the complete plugin in
`integrations/dist/claude-plugin/` by adding `skills/` (from `integrations/skills/`) and
`server/` (from `integrations/mcp/`). Edit the sources, never the dist copy.

    integrations/build.sh
    claude --plugin-dir integrations/dist/claude-plugin
