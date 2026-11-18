"""Skills follow the Agent Skills format (and OpenClaw's single-line frontmatter rule); the
build assembles a complete Claude Code plugin."""

from __future__ import annotations

import json
import re
import subprocess
import zipfile

import pytest
import yaml
from conftest import INTEGRATIONS, SKILLS

SKILL_NAMES = ["poddersum-summarize", "poddersum-ask"]


def frontmatter(path):
    text = path.read_text()
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    assert m, f"{path} has no YAML frontmatter"
    return m.group(1), text[m.end() :]


@pytest.mark.parametrize("name", SKILL_NAMES)
def test_skill_frontmatter(name: str) -> None:
    raw, body = frontmatter(SKILLS / name / "SKILL.md")
    meta = yaml.safe_load(raw)
    assert meta["name"] == name and re.fullmatch(r"[a-z0-9-]{1,64}", name)
    assert 50 < len(meta["description"]) <= 1024
    # OpenClaw: single-line keys only; metadata must be one-line JSON.
    for line in raw.splitlines():
        assert re.match(r"^[a-z][a-z-]*: \S", line), f"multi-line or odd frontmatter line: {line!r}"
    json.loads(re.search(r"^metadata: (.*)$", raw, re.M).group(1))
    assert "scripts/poddersum.py" in body and "{baseDir}" in body
    assert (SKILLS / name / "scripts" / "poddersum.py").is_file()


def test_summarize_description_covers_triggers() -> None:
    desc = yaml.safe_load(frontmatter(SKILLS / "poddersum-summarize" / "SKILL.md")[0])["description"].lower()
    for phrase in ("summarize this podcast", "what's this episode about", "worth listening",
                   "takeaways", "apple podcasts", "spotify", "rss", "not for summarizing pdfs"):  # fmt: skip
        assert phrase in desc


def test_build_assembles_plugin(tmp_path) -> None:
    out = subprocess.run([str(INTEGRATIONS / "build.sh"), "--no-wheel"], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    plugin = INTEGRATIONS / "dist" / "claude-plugin"
    manifest = json.loads((plugin / ".claude-plugin" / "plugin.json").read_text())
    assert manifest["name"] == "poddersum"
    server = json.loads((plugin / ".mcp.json").read_text())["mcpServers"]["poddersum"]
    assert "${CLAUDE_PLUGIN_ROOT}/server" in server["args"]
    for name in SKILL_NAMES:
        assert (plugin / "skills" / name / "SKILL.md").is_file()
        with zipfile.ZipFile(INTEGRATIONS / "dist" / "skills" / f"{name}.zip") as zf:
            assert f"{name}/SKILL.md" in zf.namelist()
    assert (plugin / "server" / "src" / "poddersum_mcp" / "server.py").is_file()
    assert (plugin / "server" / "uv.lock").is_file()
    assert not list(plugin.rglob("__pycache__"))


def test_no_api_key_needed_anywhere() -> None:
    """Core promise: local use needs no PodderSum or vendor key."""
    configs = [INTEGRATIONS / "claude-plugin" / ".mcp.json", *(INTEGRATIONS / "hosts").rglob("*.*")]
    for path in configs:
        assert "PODDERSUM_API_KEY" not in path.read_text(), path
    readme = (INTEGRATIONS / "README.md").read_text()
    for section in readme.split("\n### ")[1:]:
        title = section.splitlines()[0]
        if title.startswith(("Claude Duet",)):
            continue
        assert "No API key needed" in section, title
    for name in SKILL_NAMES:
        raw, body = frontmatter(SKILLS / name / "SKILL.md")
        assert "no API key" in raw and "No API key is needed" in body
        assert "engine" in body and "Never invent" in body
