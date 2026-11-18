"""Live MCP e2e against a running PodderSum API (``-m live``).

Skipped when ``PODDERSUM_API_URL`` (default http://localhost:8010) is down. To keep spend tiny
it picks the shortest recent episode of a show whose feed publishes transcripts (no speech
to text), and asks one TL;DR plus one question. Model failures (invalid/missing LLM key,
``llm_auth``/503) are environment problems and skip instead of failing.
"""

from __future__ import annotations

import os
import sys
import urllib.request

import pytest
from conftest import CLI_SCRIPT, server_env
from mcp import Client
from mcp.client.stdio import StdioServerParameters

pytestmark = pytest.mark.live

API_URL = os.environ.get("PODDERSUM_API_URL", "http://localhost:8010").rstrip("/")
# Buzzcast (Buzzsprout) publishes <podcast:transcript> tags; override to try another show.
SHOW = os.environ.get("PODDERSUM_LIVE_SHOW", "Buzzcast")
MAX_DURATION_S = 20 * 60
LLM_DOWN = ("llm_auth", "llm_config", "unavailable", "api key", "api_key", "503")


def api_up() -> bool:
    try:
        with urllib.request.urlopen(f"{API_URL}/healthz", timeout=3) as resp:
            return resp.status == 200
    except OSError:
        return False


@pytest.fixture(scope="module", autouse=True)
def _require_api() -> None:
    if not api_up():
        pytest.skip(f"PodderSum API not reachable at {API_URL}")


def params() -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "poddersum_mcp"],
        env=server_env(API_URL, None, PODDERSUM_WAIT_S="600"),
    )


def text_of(res) -> str:
    return "\n".join(c.text for c in res.content if getattr(c, "type", "") == "text")


def skip_if_llm_down(res) -> None:
    if res.is_error and any(k in text_of(res).lower() for k in LLM_DOWN):
        pytest.skip(f"LLM unavailable on the backend: {text_of(res)[:200]}")


async def test_live_search_summarize_ask() -> None:
    async with Client(params(), read_timeout_seconds=900) as client:
        found = await client.call_tool("search_podcasts", {"query": SHOW, "limit": 3})
        assert not found.is_error, text_of(found)
        results = found.structured_content["results"]
        assert results, f"no search results for {SHOW!r}"

        listing = await client.call_tool("list_episodes", {"podcast_id": results[0]["id"], "limit": 30})
        assert not listing.is_error, text_of(listing)
        episodes = listing.structured_content["episodes"]
        short = [e for e in episodes if e.get("duration_s") and e["duration_s"] <= MAX_DURATION_S]
        with_tx = [e for e in short if e["has_feed_transcript"]]
        with_tx = with_tx or [e for e in episodes if e["has_feed_transcript"]]
        pool = with_tx or short
        if not pool:
            pytest.skip("no short or feed-transcript episode found; set PODDERSUM_LIVE_SHOW")
        episode = min(pool, key=lambda e: e.get("duration_s") or 10**6)

        progress: list[float] = []

        async def on_progress(p: float, _total, _msg) -> None:
            progress.append(p)

        summary = await client.call_tool(
            "summarize_episode",
            {"episode_id": episode["id"], "style": "tldr", "wait_s": 600},
            progress_callback=on_progress,
        )
        detail = await client.call_tool("get_episode", {"episode_id": episode["id"]})
        # Ingest needs no model for a feed-transcript episode: check it before anything else.
        assert detail.structured_content["status"] == "ready", text_of(detail)
        if summary.is_error and "no transcript route" in text_of(summary):
            pytest.skip(f"API gap (server model down and no transcript route): {text_of(summary)[:300]}")
        skip_if_llm_down(summary)
        assert not summary.is_error, text_of(summary)
        status = summary.structured_content["status"]
        assert status in ("ok", "transcript"), summary.structured_content
        if status == "transcript":  # no server model: host-model path
            assert summary.structured_content["transcript"]["segments"]
            assert "(t=" in text_of(summary)
        else:
            assert "#t=" in text_of(summary)

        answer = await client.call_tool(
            "ask_episode", {"episode_id": episode["id"], "question": "Who are the hosts?"}
        )
        skip_if_llm_down(answer)
        assert not answer.is_error, text_of(answer)


async def test_live_get_transcript() -> None:
    """Host-model mode against the real API; skips naming the API gap until the route exists."""
    async with Client(params(), read_timeout_seconds=900) as client:
        found = await client.call_tool("search_podcasts", {"query": SHOW, "limit": 1})
        pid = found.structured_content["results"][0]["id"]
        listing = await client.call_tool("list_episodes", {"podcast_id": pid, "limit": 30})
        tx = [e for e in listing.structured_content["episodes"] if e["has_feed_transcript"]]
        if not tx:
            pytest.skip("no feed-transcript episode found")
        res = await client.call_tool(
            "get_transcript", {"episode_id": min(tx, key=lambda e: e.get("duration_s") or 10**6)["id"]}
        )
        if res.is_error and "no transcript route" in text_of(res):
            pytest.skip("API gap: backend has no GET /episodes/{id}/transcript route yet")
        assert not res.is_error, text_of(res)
        assert res.structured_content["transcript"]["segments"]


def test_live_cli_search() -> None:
    import subprocess

    out = subprocess.run(
        [sys.executable, "-I", str(CLI_SCRIPT), "search", SHOW],
        env=server_env(API_URL),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    assert "podcast_id" in out.stdout
