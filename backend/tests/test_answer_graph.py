"""Answer engine graph (agent.answer_graph) on the fixture transcript with a fake gateway."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent.answer_graph import FEATURE, PROMPT_VERSION, stream_answer
from agent.deps import AgentDeps
from agent.transcript_context import SegmentRef
from api.schemas import AnswerEvent, AnswerStreamEvent, ClaimEvent, ErrorEvent
from db.models import RequestRow
from llm.fake import FakeReply, provider_errors
from tests.conftest import PG_SKIP_REASON, PG_UP
from tests.fakes import (
    ANSWER_EPISODE_ID,
    BARE_EPISODE_ID,
    FAKE_ANSWER_MODEL,
    FAKE_ANSWER_MODEL_ID,
    fake_answer_gateway,
    seed_answer_episode,
)

pytestmark = [pytest.mark.db, pytest.mark.skipif(not PG_UP, reason=PG_SKIP_REASON)]

EVENT_ORDER = re.compile(r"^(status )*(claim )*(answer|error) done$")


def event_names(events: Sequence[AnswerStreamEvent]) -> str:
    return " ".join(e.event for e in events)


def mixed_reply(segs: Sequence[SegmentRef]) -> str:
    """NDJSON with 3 valid claims and 4 bad lines (bad id, garbage, empty text, no ids)."""
    lines = [
        {"text": "Founders charge too little in year one.", "segment_ids": [segs[1].id]},
        {"text": "Invented citation.", "segment_ids": [999_999]},
        "this line is not json",
        {"text": "The highest price converted well.", "segment_ids": [segs[2].id, segs[1].id]},
        {"text": "   ", "segment_ids": [segs[0].id]},
        {"text": "No citations at all.", "segment_ids": []},
    ]
    body = "\n".join(line if isinstance(line, str) else json.dumps(line) for line in lines)
    # last claim without a trailing newline (handled by the parser's close()).
    return (
        body + "\n" + json.dumps({"text": "Hire after 20 customers.", "segment_ids": [segs[3].id]})
    )


async def run(deps: AgentDeps, episode_id: str = ANSWER_EPISODE_ID, question: str = "Q?"):
    return [e async for e in stream_answer(deps, episode_id, question)]


async def request_rows(maker: async_sessionmaker[AsyncSession]) -> list[RequestRow]:
    async with maker() as s:
        return list((await s.scalars(select(RequestRow).order_by(RequestRow.created_at))).all())


@pytest.fixture
async def segs(db_sessionmaker: async_sessionmaker[AsyncSession]) -> list[SegmentRef]:
    return await seed_answer_episode(db_sessionmaker)


async def test_only_valid_claims_stream_in_order(
    db_sessionmaker: async_sessionmaker[AsyncSession], segs: list[SegmentRef]
) -> None:
    reply = FakeReply(mixed_reply(segs), tokens_in=1500, tokens_out=120, cached=1000, chunk_size=7)
    gateway, provider = fake_answer_gateway([reply])
    events = await run(AgentDeps(db_sessionmaker, gateway), question="For a founder?")

    assert EVENT_ORDER.match(event_names(events)), event_names(events)
    claims = [e for e in events if isinstance(e, ClaimEvent)]
    assert [c.index for c in claims] == list(range(len(claims))) == [0, 1, 2]
    assert [c.claim.text for c in claims] == [
        "Founders charge too little in year one.",
        "The highest price converted well.",
        "Hire after 20 customers.",
    ]
    assert claims[1].claim.citations[0].start_ms == segs[2].start_ms

    answer = next(e for e in events if isinstance(e, AnswerEvent)).answer
    assert answer.claims == [c.claim for c in claims]
    assert answer.model == FAKE_ANSWER_MODEL_ID
    assert answer.prompt_version == PROMPT_VERSION == "ask-v1+system-v1"
    assert (answer.tokens_in, answer.tokens_out, answer.cached) == (1500, 120, False)
    assert answer.cost_usd > 0

    # One model call; text only, transcript + question; no audio or file parts.
    assert len(provider.calls) == 1
    messages = provider.calls[0]["messages"]
    assert [m["role"] for m in messages] == ["system", "user"]
    assert all(isinstance(m["content"], str) for m in messages)
    assert "For a founder?" in messages[1]["content"]
    assert f"[{segs[0].id}] [00:00] Ana:" in messages[1]["content"]
    dumped = json.dumps(provider.calls[0], default=str)
    for forbidden in ("input_audio", "file_uri", "image_url", '"file"'):
        assert forbidden not in dumped

    (row,) = await request_rows(db_sessionmaker)
    assert row.feature == FEATURE == "ask"
    assert row.question == "For a founder?"
    assert row.episode_id == ANSWER_EPISODE_ID
    assert (row.tokens_in, row.tokens_out, row.tokens_cached) == (1500, 120, 1000)
    assert row.cost_usd == Decimal(str(answer.cost_usd)).quantize(Decimal("0.000001"))
    assert row.model == FAKE_ANSWER_MODEL_ID
    assert row.prompt_version == PROMPT_VERSION
    assert row.cached is False and row.error is None
    assert row.answer is not None and len(row.answer["claims"]) == 3
    assert row.latency_ms is not None and row.latency_ms >= 0


async def test_zero_grounded_claims_is_error(
    db_sessionmaker: async_sessionmaker[AsyncSession], segs: list[SegmentRef]
) -> None:
    reply = json.dumps({"text": "Invented.", "segment_ids": [999_999]}) + "\nbanana\n"
    gateway, _ = fake_answer_gateway([FakeReply(reply, tokens_in=900, tokens_out=10)])
    events = await run(AgentDeps(db_sessionmaker, gateway))

    assert event_names(events) == "status status error done"
    error = next(e for e in events if isinstance(e, ErrorEvent))
    assert error.code == "no_grounded_claims"
    (row,) = await request_rows(db_sessionmaker)
    assert row.answer is None and row.error
    assert row.tokens_in == 900  # the call happened and is still accounted


async def test_llm_error_is_error_event_then_done(
    db_sessionmaker: async_sessionmaker[AsyncSession], segs: list[SegmentRef]
) -> None:
    req = httpx.Request("POST", "https://example.invalid")
    exc = provider_errors.AuthenticationError(
        message="API_KEY_INVALID",
        llm_provider="gemini",
        model=FAKE_ANSWER_MODEL,
        response=httpx.Response(401, request=req),
    )
    gateway, _ = fake_answer_gateway([exc])
    events = await run(AgentDeps(db_sessionmaker, gateway))

    assert EVENT_ORDER.match(event_names(events))
    assert [e.event for e in events][-2:] == ["error", "done"]
    assert events[-2].code == "llm_auth"  # type: ignore[union-attr]
    (row,) = await request_rows(db_sessionmaker)
    assert row.error and row.model is None and row.tokens_in == 0


async def test_episode_without_transcript_is_not_ready(
    db_sessionmaker: async_sessionmaker[AsyncSession], segs: list[SegmentRef]
) -> None:
    gateway, provider = fake_answer_gateway(["unused"])
    events = await run(AgentDeps(db_sessionmaker, gateway), episode_id=BARE_EPISODE_ID)
    assert event_names(events) == "status error done"
    assert events[1].code == "not_ready"  # type: ignore[union-attr]
    assert provider.calls == []
    assert len(await request_rows(db_sessionmaker)) == 1


async def test_transcript_too_long_makes_no_model_call(
    db_sessionmaker: async_sessionmaker[AsyncSession], segs: list[SegmentRef]
) -> None:
    gateway, provider = fake_answer_gateway(["unused"], context_window=500)
    events = await run(AgentDeps(db_sessionmaker, gateway))
    assert events[-2].code == "transcript_too_long"  # type: ignore[union-attr]
    assert provider.calls == []
