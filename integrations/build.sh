#!/usr/bin/env bash
# Assemble PodderSum host bundles into integrations/dist/ from one source:
#   integrations/mcp/      -> the MCP server package (also built as a wheel)
#   integrations/skills/   -> Agent Skills (SKILL.md + scripts/poddersum.py)
#   integrations/claude-plugin/ -> Claude Code plugin manifest + .mcp.json
#
# Output:
#   dist/claude-plugin/            complete Claude Code plugin (claude --plugin-dir dist/claude-plugin)
#   dist/claude-plugin.zip         same, zipped (claude --plugin-dir dist/claude-plugin.zip)
#   dist/skills/<skill>/           portable skill folders (Hermes, OpenClaw, Grok, Muse, ~/.claude/skills)
#   dist/skills/<skill>.zip        one zip per skill (Claude Desktop / claude.ai skill upload)
#   dist/mcp/*.whl, *.tar.gz       poddersum-mcp wheel + sdist (uvx --from <wheel> poddersum-mcp)
#
# Usage: integrations/build.sh [--no-wheel]
#        integrations/build.sh --check   # fail if the two copies of poddersum.py differ
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIST="$ROOT/dist"
SKILLS=(poddersum-summarize poddersum-ask)
CANONICAL_SCRIPT="$ROOT/skills/poddersum-summarize/scripts/poddersum.py"

check_scripts() {
  local rc=0
  for skill in "${SKILLS[@]}"; do
    if ! cmp -s "$CANONICAL_SCRIPT" "$ROOT/skills/$skill/scripts/poddersum.py"; then
      echo "out of sync: skills/$skill/scripts/poddersum.py (canonical: skills/poddersum-summarize/scripts/poddersum.py)" >&2
      rc=1
    fi
  done
  return $rc
}

zipdir() {  # zipdir <parent> <name> <out.zip>  (python stdlib, no zip binary needed)
  (cd "$1" && python3 - "$2" "$3" <<'PY'
import os, sys, zipfile
src, out = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
    for base, dirs, files in os.walk(src):
        dirs[:] = sorted(d for d in dirs if d not in {"__pycache__", ".venv"})
        for f in sorted(files):
            path = os.path.join(base, f)
            info = zipfile.ZipInfo.from_file(path, path)
            with open(path, "rb") as fh:
                zf.writestr(info, fh.read(), zipfile.ZIP_DEFLATED)
PY
  )
}

if [[ "${1:-}" == "--check" ]]; then
  check_scripts && echo "skills scripts in sync"
  exit
fi

# One source for the CLI: copy the canonical script into every skill.
for skill in "${SKILLS[@]}"; do
  [[ "$skill" == poddersum-summarize ]] && continue
  install -m 0755 "$CANONICAL_SCRIPT" "$ROOT/skills/$skill/scripts/poddersum.py"
done

rm -rf "$DIST/claude-plugin" "$DIST/claude-plugin.zip" "$DIST/skills"   # dist/mcp kept with --no-wheel
mkdir -p "$DIST/skills" "$DIST/mcp"

copy_tree() {  # copy_tree <src> <dst>, skipping caches and venvs
  mkdir -p "$2"
  tar -C "$1" --exclude=.venv --exclude=__pycache__ --exclude='*.pyc' --exclude=dist -cf - . | tar -C "$2" -xf -
}

for skill in "${SKILLS[@]}"; do
  copy_tree "$ROOT/skills/$skill" "$DIST/skills/$skill"
  zipdir "$DIST/skills" "$skill" "$DIST/skills/$skill.zip"
done

# Claude Code plugin: manifest + .mcp.json + skills + server source (run with uv).
PLUGIN="$DIST/claude-plugin"
copy_tree "$ROOT/claude-plugin" "$PLUGIN"
rm -f "$PLUGIN/README.md"
mkdir -p "$PLUGIN/skills"
for skill in "${SKILLS[@]}"; do copy_tree "$ROOT/skills/$skill" "$PLUGIN/skills/$skill"; done
mkdir -p "$PLUGIN/server"
cp "$ROOT/mcp/pyproject.toml" "$ROOT/mcp/uv.lock" "$ROOT/mcp/README.md" "$PLUGIN/server/"
copy_tree "$ROOT/mcp/src" "$PLUGIN/server/src"
zipdir "$DIST" claude-plugin "$DIST/claude-plugin.zip"

if [[ "${1:-}" != "--no-wheel" ]]; then
  rm -rf "$DIST/mcp" && mkdir -p "$DIST/mcp"
  (cd "$ROOT/mcp" && uv build --quiet --out-dir "$DIST/mcp")
fi

check_scripts
echo "built $DIST"
