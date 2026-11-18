"""Every MCP tool and the prompt, through a real MCP client, over stdio and Streamable HTTP,
against the fake PodderSum API (recorded contract-v0 responses)."""

from __future__ import annotations

import sys

from conftest import TEST_API_KEY, fake_requests, fixture_json, free_port, server_env, stdio_params, text_of
from mcp import Client

EPISODES = fixture_json("episodes.json")["episodes"]
PODCAST = fixture_json("episodes.json")["podcast"]
EP_NEW, EP_READY, EP_STALL, EP_FAIL, EP_READY2 = (e["id"] for e in EPISODES)
AUDIO0 = "https://audio.example.com/hb/episode-0.mp3?src=feed"


async def test_lists_tools_and_prompt(connect) -> None:
    async with connect() as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
        assert set(tools) == {
            "search_podcasts", "list_episodes", "get_episode", "summarize_episode",
            "ask_episode", "chat_episode", "get_job_status", "get_transcript",
        }  # fmt: skip
        for tool in tools.values():
            assert tool.description and len(tool.description) > 40
        assert tools["search_podcasts"].annotations.read_only_hint is True
        assert tools["summarize_episode"].annotations.read_only_hint is False
        props = tools["summarize_episode"].input_schema["properties"]
        assert {"episode_id", "podcast_name", "episode_hint", "style", "angle", "engine", "wait_s"} <= set(
            props
        )
        assert set(tools["summarize_episode"].input_schema["properties"]["engine"]["anyOf"][0]["enum"]) == {
            "auto", "host", "server",
        }  # fmt: skip
        prompts = (await client.list_prompts()).prompts
        assert [p.name for p in prompts] == ["summarize_podcast"]


async def test_search_podcasts(connect) -> None:
    async with connect() as client:
        res = await client.call_tool("search_podcasts", {"query": "Hidden Brain", "limit": 1})
    assert not res.is_error
    assert "**Hidden Brain**" in text_of(res) and PODCAST["id"] in text_of(res)
    assert [r["title"] for r in res.structured_content["results"]] == ["Hidden Brain"]


async def test_search_no_results(connect) -> None:
    async with connect() as client:
        res = await client.call_tool("search_podcasts", {"query": "zzzz"})
    assert not res.is_error and "No podcasts" in text_of(res)


async def test_list_episodes(connect) -> None:
    async with connect() as client:
        res = await client.call_tool("list_episodes", {"podcast_id": PODCAST["id"], "limit": 3})
    body = text_of(res)
    assert "The Science of Second Chances" in body and "3 of 5 episodes" in body
    assert "transcript: ready" in body  # episode 1 is ready
    assert len(res.structured_content["episodes"]) == 3


async def test_get_episode_and_unknown(connect) -> None:
    async with connect() as client:
        res = await client.call_tool("get_episode", {"episode_id": EP_READY})
        missing = await client.call_tool("get_episode", {"episode_id": "0" * 64})
    assert "Sleep, Memory" in text_of(res) and "status: ready" in text_of(res)
    assert res.structured_content["audio_url"].startswith("https://audio.example.com/")
    assert missing.is_error and "Unknown episode_id" in text_of(missing)


async def test_summarize_happy_path_ingests_with_progress(connect, fake_api: str) -> None:
    progress: list[tuple[float, str | None]] = []

    async def on_progress(p: float, total: float | None, message: str | None) -> None:
        progress.append((p, message))

    async with connect() as client:
        res = await client.call_tool(
            "summarize_episode",
            {"podcast_name": "Hidden Brain", "episode_hint": "latest"},
            progress_callback=on_progress,
        )
    assert not res.is_error, text_of(res)
    body = text_of(res)
    assert "### The Science of Second Chances" in body and "**TL;DR**" in body
    assert f"[1:05]({AUDIO0}#t=65)" in body
    assert f"[1:02:05]({AUDIO0}#t=3725)" in body  # hour formatting
    data = res.structured_content
    assert data["status"] == "ok" and data["source"] == "artifact" and data["episode"]["id"] == EP_NEW
    assert data["answer"]["claims"][0]["citations"][0]["url"] == f"{AUDIO0}#t=65"
    assert progress and progress[-1][0] == 1.0
    assert any("transcribe" in (m or "") for _, m in progress)
    paths = [r["path"] for r in fake_requests(fake_api)]
    assert f"/episodes/{EP_NEW}/ingest" in paths and f"/episodes/{EP_NEW}/artifacts/tldr" in paths


async def test_summarize_ready_episode_skips_ingest(connect, fake_api: str) -> None:
    async with connect() as client:
        res = await client.call_tool("summarize_episode", {"episode_id": EP_READY, "style": "verdict"})
    body = text_of(res)
    assert "**Who it's for**" in body and "**Best 10 minutes**" in body
    assert not any(r["path"].endswith("/ingest") for r in fake_requests(fake_api))


async def test_summarize_with_angle_uses_ask(connect, fake_api: str) -> None:
    async with connect() as client:
        res = await client.call_tool(
            "summarize_episode",
            {"podcast_name": "Hidden Brain", "episode_hint": "sleep memory", "style": "takeaways",
             "angle": "for a founder"},
        )  # fmt: skip
    assert res.structured_content["source"] == "ask"
    assert res.structured_content["episode"]["id"] == EP_READY
    assert "angle: for a founder" in text_of(res) and "For founders" in text_of(res)
    asks = [r for r in fake_requests(fake_api) if r["path"].endswith("/ask")]
    assert "for a founder" in asks[0]["body"]["question"]
    assert "takeaways" in asks[0]["body"]["question"]


async def test_summarize_ambiguous_returns_candidates(connect) -> None:
    async with connect() as client:
        res = await client.call_tool(
            "summarize_episode", {"podcast_name": "Hidden Brain", "episode_hint": "rest"}
        )
    assert not res.is_error
    data = res.structured_content
    assert data["status"] == "needs_choice"
    titles = [c["title"] for c in data["candidates"]]
    assert titles[:2] == ["Rethinking Rest: Part 1", "Rethinking Rest: Part 2"]
    assert "Ask the user" in text_of(res)


async def test_summarize_by_episode_number(connect) -> None:
    async with connect() as client:
        res = await client.call_tool(
            "summarize_episode", {"podcast_name": "Hidden Brain", "episode_hint": "episode 212", "wait_s": 1}
        )
    # Episode #212's job stalls: the call returns a resumable status instead of hanging.
    data = res.structured_content
    assert data["status"] == "processing" and data["episode"]["id"] == EP_STALL
    assert data["job"]["job_id"] and "Still transcribing" in text_of(res)


async def test_summarize_failed_job_is_error(connect) -> None:
    async with connect() as client:
        res = await client.call_tool("summarize_episode", {"episode_id": EP_FAIL})
    assert res.is_error and "HTTP 403" in text_of(res)


async def test_summarize_apple_link(connect, fake_api: str) -> None:
    link = "https://podcasts.apple.com/us/podcast/hidden-brain/id1028908750"
    async with connect() as client:
        res = await client.call_tool("summarize_episode", {"podcast_name": link})
        ep_link = await client.call_tool("summarize_episode", {"podcast_name": link + "?i=1000712345678"})
    assert res.structured_content["episode"]["id"] == EP_NEW
    assert any(r["path"] == "/search" and r["query"]["q"] == "hidden brain" for r in fake_requests(fake_api))
    assert ep_link.structured_content["status"] == "needs_choice"


async def test_summarize_spotify_link_explains(connect) -> None:
    async with connect() as client:
        res = await client.call_tool(
            "summarize_episode", {"podcast_name": "https://open.spotify.com/episode/4rOoJ6Egrf8K2IrywzwOMk"}
        )
    assert res.is_error and "Spotify links cannot be resolved" in text_of(res)


async def test_summarize_needs_some_input(connect) -> None:
    async with connect() as client:
        res = await client.call_tool("summarize_episode", {})
    assert res.is_error and "podcast_name" in text_of(res)


async def test_ask_episode(connect, fake_api: str) -> None:
    async with connect() as client:
        res = await client.call_tool(
            "ask_episode", {"episode_id": EP_READY, "question": "What about founders?"}
        )
    assert "Q: What about founders?" in text_of(res)
    assert "[1:38](https://audio.example.com/hb/episode-1.mp3?src=feed#t=98)" in text_of(res)
    assert res.structured_content["answer"]["model"] == "fixture-model"


async def test_ask_stream_error_is_tool_error(connect) -> None:
    async with connect() as client:
        res = await client.call_tool("ask_episode", {"episode_id": EP_READY, "question": "[fail] anything"})
    assert res.is_error and "does not cover this question" in text_of(res)


async def test_ask_ingests_first_when_needed(connect, fake_api: str) -> None:
    async with connect() as client:
        res = await client.call_tool("ask_episode", {"episode_id": EP_NEW, "question": "Who is the guest?"})
    assert not res.is_error
    paths = [r["path"] for r in fake_requests(fake_api)]
    assert paths.index(f"/episodes/{EP_NEW}/ingest") < paths.index(f"/episodes/{EP_NEW}/ask")


async def test_chat_episode(connect, fake_api: str) -> None:
    async with connect() as client:
        res = await client.call_tool(
            "chat_episode", {"episode_id": EP_READY, "message": "And the second guest?"}
        )
    assert "You: And the second guest?" in text_of(res) and "labor economist" in text_of(res)
    assert res.structured_content["mode"] == "chat"


async def test_get_job_status(connect) -> None:
    async with connect() as client:
        started = await client.call_tool("summarize_episode", {"episode_id": EP_STALL, "wait_s": 1})
        job_id = started.structured_content["job"]["job_id"]
        res = await client.call_tool("get_job_status", {"job_id": job_id})
        missing = await client.call_tool("get_job_status", {"job_id": "nope"})
    assert f"Job `{job_id}`" in text_of(res) and res.structured_content["state"] == "running"
    assert missing.is_error and "Unknown job" in text_of(missing)


async def test_prompt(connect) -> None:
    async with connect() as client:
        prompt = await client.get_prompt(
            "summarize_podcast", {"podcast": "Hidden Brain", "angle": "for a founder"}
        )
    text = prompt.messages[0].content.text
    assert "summarize_episode" in text and "for a founder" in text and '"latest"' in text


async def test_input_validation(connect) -> None:
    async with connect() as client:
        res = await client.call_tool("ask_episode", {"episode_id": "not-an-id", "question": "x"})
    assert res.is_error


# --- transport/config specifics ------------------------------------------------------


async def test_http_forwards_api_key_stdio_sends_none(fake_api: str, http_mcp: str) -> None:
    async with Client(http_mcp) as client:
        await client.call_tool("search_podcasts", {"query": "hidden brain"})
    async with Client(stdio_params(fake_api)) as client:
        await client.call_tool("search_podcasts", {"query": "hidden brain"})
    auths = [r["authorization"] for r in fake_requests(fake_api) if r["path"] == "/search"]
    assert auths == [f"Bearer {TEST_API_KEY}", None]
    assert all(r["user_agent"].startswith("poddersum-mcp/") for r in fake_requests(fake_api))


async def test_legacy_handshake_over_stdio(fake_api: str) -> None:
    """Hosts on the pre-2026 initialize handshake (most of them today) still work."""
    async with Client(stdio_params(fake_api), mode="legacy") as client:
        res = await client.call_tool("search_podcasts", {"query": "hidden brain"})
    assert not res.is_error


async def test_api_down_message() -> None:
    url = f"http://127.0.0.1:{free_port()}"
    async with Client(stdio_params(url)) as client:
        res = await client.call_tool("summarize_episode", {"podcast_name": "Hidden Brain"})
    body = text_of(res)
    assert res.is_error and f"PodderSum API not reachable at {url}" in body
    assert "uv run uvicorn api.main:app" in body


def test_http_allowed_host_for_tunnels(fake_api: str) -> None:
    """--allowed-host admits a tunnel's Host header; other hosts stay blocked (DNS rebinding)."""
    import subprocess
    import time

    import httpx

    port = free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "poddersum_mcp", "--transport", "http", "--port", str(port),
         "--allowed-host", "tunnel.example.test"],
        env=server_env(fake_api), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )  # fmt: skip
    body = {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                   "clientInfo": {"name": "t", "version": "1"}},
    }  # fmt: skip
    headers = {"Accept": "application/json, text/event-stream"}
    try:
        for _ in range(100):
            try:
                httpx.get(f"http://127.0.0.1:{port}/mcp", timeout=0.5)
                break
            except httpx.TransportError:
                time.sleep(0.1)
        url = f"http://127.0.0.1:{port}/mcp"
        ok = httpx.post(url, json=body, headers={**headers, "Host": "tunnel.example.test"}, timeout=5)
        bad = httpx.post(url, json=body, headers={**headers, "Host": "evil.example.test"}, timeout=5)
    finally:
        proc.terminate()
        proc.wait(timeout=5)
    assert ok.status_code == 200 and bad.status_code == 421
