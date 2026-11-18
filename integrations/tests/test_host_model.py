"""Host-model mode: no server-side model needed. ``get_transcript`` + automatic fallback."""

from __future__ import annotations

import re

from conftest import fake_requests, fixture_json, set_fake, text_of

EPISODES = fixture_json("episodes.json")["episodes"]
EP_NEW, EP_READY = EPISODES[0]["id"], EPISODES[1]["id"]

SEGMENTS = fixture_json("transcript_ep0.json")["segments"]
AUDIO0 = "https://audio.example.com/hb/episode-0.mp3?src=feed"


async def test_get_transcript_ingests_and_formats(connect, fake_api: str) -> None:
    async with connect() as client:
        res = await client.call_tool("get_transcript", {"episode_id": EP_NEW})
    assert not res.is_error, text_of(res)
    body = text_of(res)
    assert "[11] 1:05 (t=65) Shankar Vedantam: Researchers keep finding" in body
    assert "[41] 1:02:05 (t=3725)" in body
    assert f"[m:ss]({AUDIO0}#t=<t>)" in body  # citation rules carry the exact link base
    data = res.structured_content
    assert data["status"] == "transcript" and data["transcript"]["next_start_ms"] is None
    assert len(data["transcript"]["segments"]) == len(SEGMENTS) == data["transcript"]["total_segments"]
    assert data["transcript"]["link_base"] == f"{AUDIO0}#t="
    paths = [r["path"] for r in fake_requests(fake_api)]
    assert paths.index(f"/episodes/{EP_NEW}/ingest") < paths.index(f"/episodes/{EP_NEW}/transcript")


async def test_get_transcript_paginates_and_ranges(connect) -> None:
    async with connect() as client:
        first = await client.call_tool("get_transcript", {"episode_id": EP_READY, "max_chars": 1000})
        nxt = first.structured_content["transcript"]["next_start_ms"]
        second = await client.call_tool(
            "get_transcript", {"episode_id": EP_READY, "max_chars": 1000, "start_ms": nxt}
        )
        stretch = await client.call_tool(
            "get_transcript", {"episode_id": EP_READY, "start_ms": 300_000, "end_ms": 342_000}
        )
    a = first.structured_content["transcript"]["segments"]
    b = second.structured_content["transcript"]["segments"]
    assert nxt is not None and a and b and a[-1]["start_ms"] < b[0]["start_ms"] == nxt
    assert f"start_ms={nxt}" in text_of(first)
    assert [s["start_ms"] for s in stretch.structured_content["transcript"]["segments"]] == [
        300_000, 312_000, 341_000,
    ]  # fmt: skip


async def test_get_transcript_by_name(connect) -> None:
    async with connect() as client:
        res = await client.call_tool(
            "get_transcript", {"podcast_name": "Hidden Brain", "episode_hint": "sleep"}
        )
    assert res.structured_content["episode"]["id"] == EP_READY


async def test_summarize_auto_falls_back_to_host(connect, fake_api: str) -> None:
    set_fake(fake_api, llm_down=True)
    async with connect() as client:
        res = await client.call_tool(
            "summarize_episode",
            {"podcast_name": "Hidden Brain", "style": "verdict", "angle": "for a founder"},
        )
    assert not res.is_error, text_of(res)
    data = res.structured_content
    assert data["status"] == "transcript" and "llm_auth" in data["reason"]
    assert "Who it's for" in data["task"] and "for a founder" in data["task"]
    assert "write the summary yourself" in text_of(res) and "(t=65)" in text_of(res)


async def test_summarize_engine_host_skips_server_model(connect, fake_api: str) -> None:
    async with connect() as client:
        res = await client.call_tool("summarize_episode", {"episode_id": EP_READY, "engine": "host"})
    assert res.structured_content["status"] == "transcript"
    assert not any("/artifacts/" in r["path"] or r["path"].endswith("/ask") for r in fake_requests(fake_api))


async def test_engine_server_returns_hint(connect, fake_api: str) -> None:
    set_fake(fake_api, llm_down=True)
    async with connect() as client:
        res = await client.call_tool("summarize_episode", {"episode_id": EP_READY, "engine": "server"})
        ask = await client.call_tool(
            "ask_episode", {"episode_id": EP_READY, "question": "Who is it for?", "engine": "server"}
        )
    for r in (res, ask):
        assert not r.is_error and r.structured_content["status"] == "model_unavailable"
        assert r.structured_content["fallback"] == {"tool": "get_transcript", "episode_id": EP_READY}
        assert "get_transcript" in text_of(r) and "never invented" in text_of(r)


async def test_ask_and_chat_fall_back(connect, fake_api: str) -> None:
    set_fake(fake_api, llm_down=True)
    async with connect() as client:
        ask = await client.call_tool(
            "ask_episode", {"episode_id": EP_READY, "question": "What about secrecy?"}
        )
        chat = await client.call_tool("chat_episode", {"episode_id": EP_READY, "message": "And the guest?"})
    assert ask.structured_content["status"] == "transcript"
    assert "What about secrecy?" in ask.structured_content["task"]
    assert chat.structured_content["status"] == "transcript"


async def test_api_gap_is_explained(connect, fake_api: str) -> None:
    set_fake(fake_api, llm_down=True, transcript_route=False)
    async with connect() as client:
        both_down = await client.call_tool("summarize_episode", {"episode_id": EP_READY})
        host = await client.call_tool("get_transcript", {"episode_id": EP_READY})
    assert both_down.is_error and "server-side model is unavailable" in text_of(both_down)
    assert "GET /episodes/{id}/transcript" in text_of(both_down)
    assert host.is_error and "no transcript route yet" in text_of(host)


async def test_server_path_unchanged_when_model_works(connect) -> None:
    async with connect() as client:
        res = await client.call_tool("summarize_episode", {"episode_id": EP_READY})
    assert res.structured_content["status"] == "ok"
    assert re.search(
        r"\[1:05\]\(https://audio\.example\.com/hb/episode-1\.mp3\?src=feed#t=65\)", text_of(res)
    )
