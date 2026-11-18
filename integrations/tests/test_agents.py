"""Skill-trigger e2e with real agent hosts, headless (``-m agents``). Spends model quota.

Each run talks to the fake PodderSum API, so "did the agent use PodderSum?" is read from the
fake API's request log (User-Agent ``poddersum-mcp/`` = MCP tools, ``poddersum-cli/`` = the
skill's script, which an agent only knows about from SKILL.md). Claude Code additionally
reports Skill / MCP tool use in its stream-json events.

Isolation: no host's global config is touched.
* Claude Code: temp cwd, ``--setting-sources project,local`` (no user settings/plugins),
  ``--plugin-dir dist/claude-plugin``, ``--no-session-persistence``, budget cap.
* Hermes: ``HERMES_HOME`` = temp dir with its own config.yaml (MCP + skills external_dirs);
  the OpenRouter key is passed through the environment from ``~/.hermes/.env`` (never printed).
* OpenClaw: ``OPENCLAW_STATE_DIR`` / ``OPENCLAW_CONFIG_PATH`` = temp dir; skills via
  ``skills.load.extraDirs`` (this OpenClaw has no native MCP client, so the script path is used).

Runs (default 7): Claude Code positive (server summary), host-model fallback (server model
down), "use my own model", 2 negatives; Hermes and OpenClaw one positive each with the server
model down (their own model writes the cited summary from the transcript). Host-written
summaries must carry >= 2 links, every one at a real segment start.
``PODDERSUM_AGENT_NEGATIVES=all`` adds the negatives on Hermes/OpenClaw;
``PODDERSUM_AGENT_HOSTS=claude,hermes,openclaw`` selects hosts.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from conftest import INTEGRATIONS, fake_requests, fixture_json, set_fake

pytestmark = pytest.mark.agents

DIST = INTEGRATIONS / "dist"
POSITIVE = "summarize this podcast for me: Hidden Brain latest episode"
OWN_MODEL = "Summarize the latest Hidden Brain episode using my own model, not PodderSum's."
SEGMENT_SECONDS = {s["start_ms"] // 1000 for s in fixture_json("transcript_ep0.json")["segments"]}
LINK = re.compile(r"https://audio\.example\.com/hb/episode-0\.mp3\?src=feed#t=(\d+)")
NEGATIVES = [
    "summarize this PDF for me: ./q3-board-report.pdf",
    "what's the weather in Paris today?",
]
HOSTS = [h.strip() for h in os.environ.get("PODDERSUM_AGENT_HOSTS", "claude,hermes,openclaw").split(",")]
NEG_HOSTS = HOSTS if os.environ.get("PODDERSUM_AGENT_NEGATIVES") == "all" else ["claude"]
MODEL_CLAUDE = os.environ.get("PODDERSUM_AGENT_MODEL_CLAUDE", "sonnet")
# Cheap OpenRouter default; deepseek-v4-pro asked for 131k max_tokens and hit a 402.
MODEL_OPENROUTER = os.environ.get("PODDERSUM_AGENT_MODEL_OPENROUTER", "deepseek/deepseek-chat")
# OpenClaw 2026.2.x only accepts models in its own catalog (`openclaw models list --all`).
MODEL_OPENCLAW = os.environ.get("PODDERSUM_AGENT_MODEL_OPENCLAW", "openrouter/deepseek/deepseek-chat")
TIMEOUT_S = 420
BILLING_ERRORS = ("credits exhausted", "http 402", "requires more credits", "insufficient credits")


@pytest.fixture(scope="module", autouse=True)
def _build() -> None:
    subprocess.run([str(INTEGRATIONS / "build.sh"), "--no-wheel"], check=True, capture_output=True)


def poddersum_usage(api_url: str) -> dict[str, int]:
    reqs = [r for r in fake_requests(api_url) if not r["path"].startswith("/_fake")]
    ua = [r.get("user_agent") or "" for r in reqs]
    return {
        "requests": len(reqs),
        "mcp": sum(u.startswith("poddersum-mcp/") for u in ua),
        "script": sum(u.startswith("poddersum-cli/") for u in ua),
        "summary": sum("/artifacts/" in r["path"] or r["path"].endswith("/ask") for r in reqs),
        "transcript": sum(r["path"].endswith("/transcript") for r in reqs),
    }


def assert_grounded_links(text: str) -> None:
    """Host-written summary: >= 2 links, each at a real segment start (never invented)."""
    seconds = [int(t) for t in LINK.findall(text)]
    assert len(seconds) >= 2, f"expected timestamp links, got: {text[-1500:]}"
    invented = sorted(set(seconds) - SEGMENT_SECONDS)
    assert not invented, f"links not at a segment start: {invented}"


def report(host: str, prompt: str, **facts) -> None:
    print(f"\n[agents] host={host} prompt={prompt!r} " + " ".join(f"{k}={v}" for k, v in facts.items()))


def openrouter_key() -> str | None:
    if os.environ.get("OPENROUTER_API_KEY"):
        return os.environ["OPENROUTER_API_KEY"]
    env_file = Path.home() / ".hermes" / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("OPENROUTER_API_KEY="):
                return line.split("=", 1)[1].strip().strip("'\"") or None
    return None


def skip_on_billing(text: str, host: str) -> None:
    if any(b in text.lower() for b in BILLING_ERRORS):
        pytest.skip(f"{host}: model provider refused for billing/credits (no PodderSum issue)")


# --- Claude Code -----------------------------------------------------------------------


def run_claude(prompt: str, api_url: str, tmp_path: Path) -> dict:
    if not shutil.which("claude"):
        pytest.skip("claude CLI not installed")
    cwd = tmp_path / "cc"
    cwd.mkdir()
    cmd = [
        "claude", "-p", prompt, "--output-format", "stream-json", "--verbose",
        "--plugin-dir", str(DIST / "claude-plugin"), "--setting-sources", "project,local",
        "--no-session-persistence", "--model", MODEL_CLAUDE, "--max-budget-usd", "0.75",
        "--allowedTools", "Skill", "mcp__plugin_poddersum_poddersum", "Bash(python3:*)",
    ]  # fmt: skip
    env = {**os.environ, "PODDERSUM_API_URL": api_url}
    out = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=TIMEOUT_S)
    events = [json.loads(line) for line in out.stdout.splitlines() if line.startswith("{")]
    skills, tools, text, cost = [], [], [], None
    for ev in events:
        if ev.get("type") == "assistant":
            for block in ev["message"]["content"]:
                if block.get("type") == "tool_use":
                    tools.append(block["name"])
                    if block["name"] == "Skill":
                        skills.append(str(block["input"].get("skill") or block["input"]))
                    if block["name"] == "Bash" and "poddersum.py" in block["input"].get("command", ""):
                        tools.append("script:poddersum.py")
                elif block.get("type") == "text":
                    text.append(block["text"])
        elif ev.get("type") == "result":
            cost = ev.get("total_cost_usd")
    init = next((e for e in events if e.get("subtype") == "init"), {})
    return {
        "returncode": out.returncode,
        "skills": skills,
        "tools": tools,
        "text": "\n".join(text),
        "cost": cost,
        "loaded_skills": [s for s in init.get("skills", []) if "poddersum" in s],
        "mcp_status": {
            s["name"]: s["status"] for s in init.get("mcp_servers", []) if "poddersum" in s["name"]
        },
        "stderr": out.stderr[-2000:],
    }


def test_claude_positive(fake_api: str, tmp_path: Path) -> None:
    if "claude" not in HOSTS:
        pytest.skip("host not selected")
    run = run_claude(POSITIVE, fake_api, tmp_path)
    usage = poddersum_usage(fake_api)
    skill_used = any("poddersum" in s for s in run["skills"])
    tool_used = any("poddersum" in t for t in run["tools"])
    report("claude", POSITIVE, skill_invoked=skill_used, tools=run["tools"], usage=usage, cost=run["cost"])
    # "pending" = still starting (first run after a rebuild syncs the plugin's venv).
    assert run["mcp_status"].get("plugin:poddersum:poddersum") in ("connected", "pending"), run
    assert set(run["loaded_skills"]) == {"poddersum:poddersum-summarize", "poddersum:poddersum-ask"}
    assert tool_used, run
    assert usage["summary"] >= 1, usage
    assert "#t=" in run["text"], "answer should keep timestamp links"


def test_claude_host_model_fallback(fake_api: str, tmp_path: Path) -> None:
    """No server-side model (like today's backend): Claude writes the summary from the transcript."""
    if "claude" not in HOSTS:
        pytest.skip("host not selected")
    set_fake(fake_api, llm_down=True)
    run = run_claude(POSITIVE, fake_api, tmp_path)
    usage = poddersum_usage(fake_api)
    report("claude", POSITIVE + " [llm down]", tools=run["tools"], usage=usage, cost=run["cost"])
    assert any("poddersum" in t for t in run["tools"]), run
    assert usage["transcript"] >= 1, usage
    assert_grounded_links(run["text"])


def test_claude_own_model_request(fake_api: str, tmp_path: Path) -> None:
    """'use my own model' -> engine=host: no server-side summary is requested."""
    if "claude" not in HOSTS:
        pytest.skip("host not selected")
    run = run_claude(OWN_MODEL, fake_api, tmp_path)
    usage = poddersum_usage(fake_api)
    report("claude", OWN_MODEL, tools=run["tools"], usage=usage, cost=run["cost"])
    assert usage["transcript"] >= 1 and usage["summary"] == 0, usage
    assert_grounded_links(run["text"])


@pytest.mark.parametrize("prompt", NEGATIVES)
def test_claude_negative(fake_api: str, tmp_path: Path, prompt: str) -> None:
    if "claude" not in NEG_HOSTS:
        pytest.skip("host not selected")
    run = run_claude(prompt, fake_api, tmp_path)
    usage = poddersum_usage(fake_api)
    report("claude", prompt, skills=run["skills"], tools=run["tools"], usage=usage, cost=run["cost"])
    assert not any("poddersum" in s for s in run["skills"]), run["skills"]
    assert not any("poddersum" in t for t in run["tools"]), run["tools"]
    assert usage["requests"] == 0, usage


# --- Hermes ----------------------------------------------------------------------------


def run_hermes(prompt: str, api_url: str, tmp_path: Path) -> subprocess.CompletedProcess:
    if not shutil.which("hermes"):
        pytest.skip("hermes CLI not installed")
    key = openrouter_key()
    if not key:
        pytest.skip("no OPENROUTER_API_KEY for an isolated Hermes run")
    home = tmp_path / "hermes-home"
    home.mkdir()
    (home / "config.yaml").write_text(
        "model:\n"
        f"  default: {MODEL_OPENROUTER}\n"
        "  provider: openrouter\n"
        "mcp_servers:\n"
        "  poddersum:\n"
        f"    command: {json.dumps(sys.executable)}\n"
        '    args: ["-m", "poddersum_mcp"]\n'
        f"    env: {{PODDERSUM_API_URL: {json.dumps(api_url)}}}\n"
        "    timeout: 300\n"
        "skills:\n"
        f"  external_dirs: [{json.dumps(str(DIST / 'skills'))}]\n"
    )
    cwd = tmp_path / "hermes-cwd"
    cwd.mkdir()
    env = {k: v for k, v in os.environ.items() if k not in ("HERMES_PROFILE",)}
    env.update(HERMES_HOME=str(home), HOME=str(tmp_path), OPENROUTER_API_KEY=key, PODDERSUM_API_URL=api_url)
    cmd = ["hermes", "-z", prompt, "--usage-file", str(tmp_path / "usage.json"), "--accept-hooks"]
    return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=TIMEOUT_S)


def test_hermes_positive(fake_api: str, tmp_path: Path) -> None:
    if "hermes" not in HOSTS:
        pytest.skip("host not selected")
    set_fake(fake_api, llm_down=True)  # host-model path: Hermes' own model writes the summary
    out = run_hermes(POSITIVE, fake_api, tmp_path)
    skip_on_billing(out.stdout + out.stderr, "hermes")
    usage = poddersum_usage(fake_api)
    report("hermes", POSITIVE + " [llm down]", returncode=out.returncode, usage=usage)
    assert usage["mcp"] + usage["script"] >= 1, out.stdout[-1500:]
    assert usage["transcript"] >= 1, usage
    assert_grounded_links(out.stdout)


@pytest.mark.parametrize("prompt", NEGATIVES)
def test_hermes_negative(fake_api: str, tmp_path: Path, prompt: str) -> None:
    if "hermes" not in NEG_HOSTS:
        pytest.skip("negatives run on Claude Code only (PODDERSUM_AGENT_NEGATIVES=all to include)")
    out = run_hermes(prompt, fake_api, tmp_path)
    skip_on_billing(out.stdout + out.stderr, "hermes")
    assert poddersum_usage(fake_api)["requests"] == 0


# --- OpenClaw --------------------------------------------------------------------------


def run_openclaw(prompt: str, api_url: str, tmp_path: Path) -> subprocess.CompletedProcess:
    if not shutil.which("openclaw"):
        pytest.skip("openclaw CLI not installed")
    key = openrouter_key()
    if not key:
        pytest.skip("no OPENROUTER_API_KEY for an isolated OpenClaw run")
    state = tmp_path / "openclaw-state"
    (state / "workspace").mkdir(parents=True)
    config = {
        "agents": {
            "defaults": {
                "workspace": str(state / "workspace"),
                "model": {"primary": MODEL_OPENCLAW},
            }
        },
        "skills": {"load": {"extraDirs": [str(DIST / "skills")]}},
    }
    (state / "openclaw.json").write_text(json.dumps(config, indent=2))
    env = {
        **os.environ,
        "OPENCLAW_STATE_DIR": str(state),
        "OPENCLAW_CONFIG_PATH": str(state / "openclaw.json"),
        "HOME": str(tmp_path),
        "OPENROUTER_API_KEY": key,
        "PODDERSUM_API_URL": api_url,
    }
    cmd = ["openclaw", "agent", "--local", "--message", prompt, "--json",
           "--session-id", str(uuid.uuid4()), "--timeout", "300"]  # fmt: skip
    return subprocess.run(
        cmd, cwd=state / "workspace", env=env, capture_output=True, text=True, timeout=TIMEOUT_S
    )


def test_openclaw_positive(fake_api: str, tmp_path: Path) -> None:
    if "openclaw" not in HOSTS:
        pytest.skip("host not selected")
    set_fake(fake_api, llm_down=True)  # host-model path: OpenClaw's own model writes the summary
    out = run_openclaw(POSITIVE, fake_api, tmp_path)
    skip_on_billing(out.stdout + out.stderr, "openclaw")
    if "Unknown model" in out.stdout + out.stderr:
        pytest.skip(f"openclaw does not know {MODEL_OPENCLAW}; set PODDERSUM_AGENT_MODEL_OPENCLAW")
    usage = poddersum_usage(fake_api)
    (tmp_path / "openclaw-output.json").write_text(out.stdout)  # for debugging a failed run
    report(
        "openclaw",
        POSITIVE + " [llm down]",
        returncode=out.returncode,
        usage=usage,
        output=tmp_path / "openclaw-output.json",
    )
    assert usage["script"] >= 1, (out.stdout + out.stderr)[-1500:]
    assert usage["transcript"] >= 1, usage
    assert_grounded_links(out.stdout)


@pytest.mark.parametrize("prompt", NEGATIVES)
def test_openclaw_negative(fake_api: str, tmp_path: Path, prompt: str) -> None:
    if "openclaw" not in NEG_HOSTS:
        pytest.skip("negatives run on Claude Code only (PODDERSUM_AGENT_NEGATIVES=all to include)")
    out = run_openclaw(prompt, fake_api, tmp_path)
    skip_on_billing(out.stdout + out.stderr, "openclaw")
    assert poddersum_usage(fake_api)["requests"] == 0
