"""Episode chat (agent.chat_graph) on the Postgres checkpointer, with a fake gateway.

The checkpointer's tables are created in the session's throwaway schema (search_path
pinned via the connection string), and each test deletes its threads before and after,
so the suite is re-runnable.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from collections.abc import AsyncIterator, Sequence
from pathlib import Path

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent import checkpointer as cp
from agent.chat_graph import (
    MAX_PRIOR_MESSAGES,
    PROMPT_VERSION,
    get_chat_history,
    stream_chat,
)
from agent.deps import AgentDeps
from agent.transcript_context import SegmentRef, render_transcript
from api.schemas import AnswerEvent, AnswerStreamEvent, ChatRole, ClaimEvent, ErrorEvent
from db.models import Episode, RequestRow
from llm.fake import FakeReply, provider_errors
from tests.conftest import LOCAL_SETTINGS, PG_SKIP_REASON, PG_UP
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
UNKNOWN_EPISODE_ID = "f" * 64
TEST_EPISODES = (ANSWER_EPISODE_ID, BARE_EPISODE_ID, UNKNOWN_EPISODE_ID)


def ndjson(*claims: tuple[str, list[int]]) -> str:
    return "\n".join(json.dumps({"text": t, "segment_ids": ids}) for t, ids in claims)


def names(events: Sequence[AnswerStreamEvent]) -> str:
    return " ".join(e.event for e in events)


@pytest.fixture
def conninfo(db_schema: str) -> str:
    return f"{LOCAL_SETTINGS.database_url_libpq}?options=-csearch_path%3D{db_schema}"


@pytest.fixture
async def saver(conninfo: str) -> AsyncIterator:
    await cp.close_checkpointer()  # never reuse a saver bound to another test's loop
    opened = await cp.open_checkpointer(conninfo=conninfo)
    for ep in TEST_EPISODES:
        await opened.adelete_thread(cp.thread_id_for(ep))
    try:
        yield opened
    finally:
        current = await cp.open_checkpointer(conninfo=conninfo)
        for ep in TEST_EPISODES:
            await current.adelete_thread(cp.thread_id_for(ep))
        await cp.close_checkpointer()


@pytest.fixture
async def segs(db_sessionmaker: async_sessionmaker[AsyncSession]) -> list[SegmentRef]:
    return await seed_answer_episode(db_sessionmaker)


async def chat(deps: AgentDeps, saver, message: str, episode_id: str = ANSWER_EPISODE_ID):
    return [e async for e in stream_chat(deps, saver, episode_id, message)]


async def chat_rows(maker: async_sessionmaker[AsyncSession]) -> list[RequestRow]:
    async with maker() as s:
        stmt = (
            select(RequestRow).where(RequestRow.feature == "chat").order_by(RequestRow.created_at)
        )
        return list((await s.scalars(stmt)).all())


def test_thread_id_format() -> None:
    assert cp.thread_id_for("ab" * 32) == "ep:" + "ab" * 32


async def test_multi_turn_survives_checkpointer_restart(
    db_sessionmaker: async_sessionmaker[AsyncSession],
    segs: list[SegmentRef],
    saver,
    conninfo: str,
) -> None:
    replies = [
        FakeReply(ndjson(("Founders undercharge in year one.", [segs[1].id])), tokens_in=900),
        FakeReply(
            ndjson(("The top price converted well.", [segs[2].id]), ("Bad.", [999_999])),
            tokens_in=950,
        ),
        FakeReply(ndjson(("They hired after twenty customers.", [segs[3].id]))),
    ]
    gateway, provider = fake_answer_gateway(replies)
    deps = AgentDeps(db_sessionmaker, gateway)

    first = await chat(deps, saver, "What is the biggest pricing mistake?")
    second = await chat(deps, saver, "What did their price test show?")
    for events in (first, second):
        assert EVENT_ORDER.match(names(events)), names(events)
    assert [e.index for e in second if isinstance(e, ClaimEvent)] == [0]

    # Simulate an API restart: drop the pool and open a fresh saver.
    await cp.close_checkpointer()
    reopened = await cp.open_checkpointer(conninfo=conninfo)
    assert reopened is not saver

    history = await get_chat_history(reopened, ANSWER_EPISODE_ID)
    assert [m.role for m in history] == [ChatRole.user, ChatRole.assistant] * 2
    assert history[0].text == "What is the biggest pricing mistake?"
    assert history[1].text == "Founders undercharge in year one."
    assert history[3].text == "The top price converted well."  # invalid claim dropped
    assert history[0].claims == []
    assert len({m.id for m in history}) == 4
    for m in history:
        uuid.UUID(m.id)
    created = [m.created_at for m in history]
    assert created == sorted(created)

    third = await chat(deps, reopened, "And hiring?")
    assert EVENT_ORDER.match(names(third))
    prompt = provider.calls[2]["messages"][1]["content"]
    assert "User: What is the biggest pricing mistake?" in prompt
    assert f"- Founders undercharge in year one. (cited: {segs[1].id})" in prompt
    assert "User: What did their price test show?" in prompt
    assert "<new_message>\nAnd hiring?\n</new_message>" in prompt

    after = await get_chat_history(reopened, ANSWER_EPISODE_ID)
    assert len(after) == 6 and [m.id for m in after[:4]] == [m.id for m in history]


async def test_citations_valid_on_every_assistant_turn_and_prefix_matches_ask(
    db_sessionmaker: async_sessionmaker[AsyncSession], segs: list[SegmentRef], saver
) -> None:
    by_id = {s.id: s for s in segs}
    replies = [
        ndjson(("A.", [segs[0].id, 999_999, segs[0].id]), ("B.", [segs[4].id])),
        ndjson(("C.", [segs[5].id, segs[1].id])),
    ]
    gateway, provider = fake_answer_gateway(replies)
    deps = AgentDeps(db_sessionmaker, gateway)
    await chat(deps, saver, "one")
    await chat(deps, saver, "two")

    for m in await get_chat_history(saver, ANSWER_EPISODE_ID):
        if m.role is ChatRole.assistant:
            assert m.claims
            for claim in m.claims:
                assert 1 <= len(claim.citations) <= 3
                for c in claim.citations:
                    seg = by_id[c.segment_id]
                    assert (c.start_ms, c.end_ms, c.speaker) == (
                        seg.start_ms,
                        seg.end_ms,
                        seg.speaker,
                    )
            assert m.text == " ".join(c.text for c in m.claims)

    # Same cacheable prefix as ask: system prompt + transcript, text only.
    for call in provider.calls:
        system, user = call["messages"]
        assert system["role"] == "system" and isinstance(system["content"], str)
        assert user["content"].startswith(render_transcript(segs) + "\n\n")
    assert provider.calls[0]["messages"][0] == provider.calls[1]["messages"][0]


async def test_answer_event_and_request_row(
    db_sessionmaker: async_sessionmaker[AsyncSession], segs: list[SegmentRef], saver
) -> None:
    reply = FakeReply(ndjson(("A.", [segs[1].id])), tokens_in=1200, tokens_out=40, cached=1000)
    gateway, provider = fake_answer_gateway([reply])
    events = await chat(AgentDeps(db_sessionmaker, gateway), saver, "hello?")
    assert provider.calls[0]["model"] == FAKE_ANSWER_MODEL
    assert provider.calls[0]["stream"] is True
    answer = next(e for e in events if isinstance(e, AnswerEvent)).answer
    assert answer.prompt_version == PROMPT_VERSION == "chat-v1+system-v1"
    assert answer.model == FAKE_ANSWER_MODEL_ID and answer.cached is False
    assert answer.claims == [e.claim for e in events if isinstance(e, ClaimEvent)]
    (row,) = await chat_rows(db_sessionmaker)
    assert (row.question, row.model, row.prompt_version) == (
        "hello?",
        FAKE_ANSWER_MODEL_ID,
        PROMPT_VERSION,
    )
    assert (row.tokens_in, row.tokens_out, row.tokens_cached) == (1200, 40, 1000)
    assert float(row.cost_usd) > 0 and row.error is None and row.answer is not None


async def test_no_grounded_claims_persists_no_turn(
    db_sessionmaker: async_sessionmaker[AsyncSession], segs: list[SegmentRef], saver
) -> None:
    gateway, _ = fake_answer_gateway(
        [ndjson(("ok", [segs[0].id])), ndjson(("Invented.", [999_999])) + "\nbanana"]
    )
    deps = AgentDeps(db_sessionmaker, gateway)
    await chat(deps, saver, "first")
    events = await chat(deps, saver, "second")
    assert names(events) == "status status error done"
    assert next(e for e in events if isinstance(e, ErrorEvent)).code == "no_grounded_claims"
    history = await get_chat_history(saver, ANSWER_EPISODE_ID)
    assert [m.text for m in history] == ["first", "ok"]
    rows = await chat_rows(db_sessionmaker)
    assert len(rows) == 2 and rows[1].error and rows[1].answer is None
    assert rows[1].tokens_in > 0  # the failed call is still accounted


async def test_llm_error_is_error_then_done(
    db_sessionmaker: async_sessionmaker[AsyncSession], segs: list[SegmentRef], saver
) -> None:
    exc = provider_errors.RateLimitError(
        message="slow down",
        llm_provider="gemini",
        model=FAKE_ANSWER_MODEL,
        response=httpx.Response(429, request=httpx.Request("POST", "https://x.invalid")),
    )
    gateway, _ = fake_answer_gateway([exc])
    events = await chat(AgentDeps(db_sessionmaker, gateway), saver, "hi")
    assert names(events) == "status status error done"
    assert events[2].code == "llm_rate_limited"  # type: ignore[union-attr]
    assert await get_chat_history(saver, ANSWER_EPISODE_ID) == []


async def test_episode_without_transcript_is_not_ready(
    db_sessionmaker: async_sessionmaker[AsyncSession], segs: list[SegmentRef], saver
) -> None:
    gateway, provider = fake_answer_gateway(["unused"])
    events = await chat(AgentDeps(db_sessionmaker, gateway), saver, "hi", BARE_EPISODE_ID)
    assert names(events) == "status error done"
    assert events[1].code == "not_ready"  # type: ignore[union-attr]
    assert provider.calls == []


async def test_history_empty_without_chat(segs: list[SegmentRef], saver) -> None:
    assert await get_chat_history(saver, BARE_EPISODE_ID) == []
    assert await get_chat_history(saver, UNKNOWN_EPISODE_ID) == []
    assert await get_chat_history(saver, ANSWER_EPISODE_ID) == []


async def test_prior_turns_are_bounded(
    db_sessionmaker: async_sessionmaker[AsyncSession], segs: list[SegmentRef], saver
) -> None:
    exchanges = MAX_PRIOR_MESSAGES // 2 + 1
    replies = [ndjson((f"reply-{i}.", [segs[i % len(segs)].id])) for i in range(exchanges + 1)]
    gateway, provider = fake_answer_gateway(replies)
    deps = AgentDeps(db_sessionmaker, gateway)
    for i in range(exchanges + 1):
        await chat(deps, saver, f"question-{i}")
    last_prompt = provider.calls[-1]["messages"][1]["content"]
    assert "question-0" not in last_prompt and "reply-0." not in last_prompt
    assert f"question-{exchanges - 1}" in last_prompt
    assert last_prompt.count("User: ") == MAX_PRIOR_MESSAGES // 2
    history = await get_chat_history(saver, ANSWER_EPISODE_ID)
    assert len(history) == 2 * (exchanges + 1)  # full history kept in the checkpoint


async def test_concurrent_messages_keep_both_turns(
    db_sessionmaker: async_sessionmaker[AsyncSession], segs: list[SegmentRef], saver
) -> None:
    gateway, _ = fake_answer_gateway([ndjson(("x.", [segs[0].id]))])
    deps = AgentDeps(db_sessionmaker, gateway)
    await asyncio.gather(chat(deps, saver, "a"), chat(deps, saver, "b"))
    history = await get_chat_history(saver, ANSWER_EPISODE_ID)
    assert sorted(m.text for m in history if m.role is ChatRole.user) == ["a", "b"]
    assert [m.role for m in history] == [ChatRole.user, ChatRole.assistant] * 2


@respx.mock(assert_all_mocked=True, assert_all_called=False)
async def test_chat_works_when_audio_url_is_gone(
    respx_mock: respx.MockRouter,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    segs: list[SegmentRef],
    saver,
) -> None:
    async with db_sessionmaker() as s:
        audio_url = (await s.get(Episode, ANSWER_EPISODE_ID)).audio_url  # type: ignore[union-attr]
    audio = respx_mock.route(url=audio_url).mock(return_value=httpx.Response(404))
    gateway, _ = fake_answer_gateway([ndjson(("Pricing.", [segs[0].id]))])
    events = await chat(AgentDeps(db_sessionmaker, gateway), saver, "about?")
    assert isinstance(events[-2], AnswerEvent)
    assert audio.call_count == 0  # the stored transcript is all chat needs
    assert respx_mock.calls.call_count == 0  # and no other HTTP request was made


def test_no_legacy_persistence_or_audio_upload_under_backend() -> None:
    backend = Path(__file__).resolve().parents[1]
    pattern = re.compile(r"SqliteSaver|checkpoints\.db|file_uri|files\.upload")
    skip = {".venv", "__pycache__", "tests", ".pytest_cache", ".ruff_cache"}  # tests assert absence
    hits = [
        str(p.relative_to(backend))
        for p in backend.rglob("*")
        if p.is_file() and not skip.intersection(p.relative_to(backend).parts)
        if pattern.search(p.read_text(encoding="utf-8", errors="ignore"))
    ]
    assert hits == []


async def test_open_is_idempotent_and_lifespan_closes(conninfo: str) -> None:
    await cp.close_checkpointer()
    async with cp.checkpointer_lifespan(conninfo=conninfo) as saver:
        assert await cp.open_checkpointer() is saver  # idempotent; no second pool/setup
        await saver.setup()  # LangGraph's migrations are themselves idempotent
    reopened = await cp.open_checkpointer(conninfo=conninfo)
    assert reopened is not saver
    await cp.close_checkpointer()
    await cp.close_checkpointer()  # closing twice is harmless
