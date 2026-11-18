"""HTTP contract for ask (SSE) and artifacts (JSON) with a fake gateway on app.state."""

from __future__ import annotations

import json
import re

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.schemas import Artifact
from tests.conftest import PG_SKIP_REASON, PG_UP
from tests.fakes import ANSWER_EPISODE_ID, fake_answer_gateway, seed_answer_episode

pytestmark = [pytest.mark.db, pytest.mark.skipif(not PG_UP, reason=PG_SKIP_REASON)]

EVENT_ORDER = re.compile(r"^(status )*(claim )*(answer|error) done$")


def parse_sse(body: str) -> list[tuple[str, dict]]:
    """(event name, data JSON) per frame; comment/ping frames are skipped."""
    frames = []
    for block in re.split(r"\r?\n\r?\n", body):
        name, data = None, []
        for line in block.splitlines():
            if line.startswith("event:"):
                name = line.removeprefix("event:").strip()
            elif line.startswith("data:"):
                data.append(line.removeprefix("data:").strip())
        if name and data:
            frames.append((name, json.loads("\n".join(data))))
    return frames


async def test_ask_sse_frames_match_contract(
    app: FastAPI, client: httpx.AsyncClient, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    segs = await seed_answer_episode(db_sessionmaker)
    reply = "\n".join(
        json.dumps(c)
        for c in [
            {"text": "Charge more.", "segment_ids": [segs[1].id]},
            {"text": "Invented.", "segment_ids": [999_999]},
            {"text": "Hire late.", "segment_ids": [segs[3].id]},
        ]
    )
    app.state.gateway, provider = fake_answer_gateway([reply])

    resp = await client.post(
        f"/episodes/{ANSWER_EPISODE_ID}/ask", json={"question": "For a founder?"}
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    frames = parse_sse(resp.text)
    assert all(name == data["event"] for name, data in frames)
    names = " ".join(name for name, _ in frames)
    assert EVENT_ORDER.match(names), names
    claims = [d for n, d in frames if n == "claim"]
    assert [c["index"] for c in claims] == [0, 1]
    answer = next(d for n, d in frames if n == "answer")["answer"]
    assert [c["text"] for c in answer["claims"]] == ["Charge more.", "Hire late."]
    assert answer["claims"][0]["citations"][0]["start_ms"] == segs[1].start_ms
    assert len(provider.calls) == 1


async def test_artifact_second_request_is_cached(
    app: FastAPI, client: httpx.AsyncClient, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    segs = await seed_answer_episode(db_sessionmaker)
    reply = json.dumps({"claims": [{"text": "About pricing.", "segment_ids": [segs[0].id]}]})
    app.state.gateway, provider = fake_answer_gateway([reply])
    url = f"/episodes/{ANSWER_EPISODE_ID}/artifacts/tldr"

    first = await client.get(url)
    assert first.status_code == 200, first.text
    assert Artifact.model_validate(first.json()).answer.cached is False
    assert len(provider.calls) == 1

    second = await client.get(url)
    assert second.status_code == 200
    body = Artifact.model_validate(second.json())
    assert body.answer.cached is True
    assert body.answer.claims[0].text == "About pricing."
    assert len(provider.calls) == 1


async def test_artifact_model_failure_is_clean_http_error(
    app: FastAPI, client: httpx.AsyncClient, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    await seed_answer_episode(db_sessionmaker)
    bad = json.dumps({"claims": [{"text": "Invented.", "segment_ids": [999_999]}]})
    app.state.gateway, _ = fake_answer_gateway([bad])
    resp = await client.get(f"/episodes/{ANSWER_EPISODE_ID}/artifacts/takeaways")
    assert resp.status_code == 502
    assert resp.json()["code"] == "no_grounded_claims"
