"""Migration 0003: artifact cache helpers and request accounting against the throwaway
Postgres schema."""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.schemas import Answer, ArtifactKind
from db import queries
from db.models import ArtifactRow, Episode, Podcast, RequestRow
from tests.conftest import PG_SKIP_REASON, PG_UP

pytestmark = [pytest.mark.db, pytest.mark.skipif(not PG_UP, reason=PG_SKIP_REASON)]

POD = "a" * 64
EP = "b" * 64


def _answer(text: str = "It is about testing.", version: str = "tldr-v1") -> dict:
    return Answer(
        claims=[{"text": text, "citations": [{"segment_id": 1, "start_ms": 1500}]}],
        model="fake-model-1",
        prompt_version=version,
        tokens_in=1200,
        tokens_out=80,
        cost_usd=0.000321,
        cached=False,
    ).model_dump(mode="json")


@pytest.fixture
async def maker(db_sessionmaker: async_sessionmaker[AsyncSession]):
    async with db_sessionmaker() as s:
        s.add(Podcast(id=POD, feed_url="https://feed.example/rss", title="Pod"))
        await s.flush()
        s.add(Episode(id=EP, podcast_id=POD, guid="g", title="Ep", audio_url="https://a/e.mp3"))
        await s.commit()
    return db_sessionmaker


async def _save(s: AsyncSession, version: str = "tldr-v1", text: str = "It is about testing."):
    return await queries.save_artifact(
        s,
        episode_id=EP,
        kind=ArtifactKind.tldr.value,
        prompt_version=version,
        answer=_answer(text, version),
        model="fake-model-1",
        tokens_in=1200,
        tokens_out=80,
        cost_usd=0.000321,
    )


async def test_artifact_miss_save_hit_and_version_bump(maker) -> None:
    async with maker() as s:
        assert await queries.get_artifact(s, EP, "tldr", "tldr-v1") is None
        saved = await _save(s)
        await s.commit()
        assert saved.id is not None and saved.created_at is not None
    async with maker() as s:
        hit = await queries.get_artifact(s, EP, "tldr", "tldr-v1")
        assert hit is not None and hit.id == saved.id
        assert Answer.model_validate(hit.answer).claims[0].citations[0].start_ms == 1500
        assert hit.cost_usd == Decimal("0.000321") and hit.tokens_in == 1200
        assert await queries.get_artifact(s, EP, "tldr", "tldr-v2") is None  # version bump
        assert await queries.get_artifact(s, EP, "verdict", "tldr-v1") is None
        # saving again with the same key keeps the first stored answer
        again = await _save(s, text="A different answer.")
        assert again.id == saved.id
        assert again.answer["claims"][0]["text"] == "It is about testing."


async def test_concurrent_save_artifact_leaves_one_row(maker) -> None:
    async with maker() as s1, maker() as s2:
        a = await _save(s1, text="first")  # s1 holds the uncommitted row
        task = asyncio.create_task(_save(s2, text="second"))  # blocks on the unique key
        await asyncio.sleep(0.2)
        assert not task.done()
        await s1.commit()
        b = await task
        await s2.commit()
        assert a.id == b.id
        assert b.answer["claims"][0]["text"] == "first"
    async with maker() as s:
        n = await s.scalar(select(func.count()).select_from(ArtifactRow))
        assert n == 1


async def test_unknown_artifact_kind_rejected(maker) -> None:
    async with maker() as s:
        with pytest.raises(IntegrityError, match="ck_artifacts_kind"):
            await queries.save_artifact(
                s,
                episode_id=EP,
                kind="haiku",
                prompt_version="v1",
                answer={},
                model="m",
                tokens_in=0,
                tokens_out=0,
                cost_usd=0,
            )


async def test_record_request_round_trip_and_list(maker) -> None:
    answer = _answer(version="ask-v1")
    async with maker() as s:
        r1 = await queries.record_request(
            s,
            episode_id=EP,
            feature="ask",
            question="Summarize for a founder",
            answer=answer,
            prompt_version="ask-v1",
            model="fake-model-1",
            tokens_in=1200,
            tokens_out=80,
            tokens_cached=1000,
            cost_usd=0.000321,
            latency_ms=950,
        )
        r2 = await queries.record_request(
            s,
            episode_id=EP,
            feature="preset",
            kind="tldr",
            answer=None,
            prompt_version="tldr-v1",
            cached=True,
        )
        await s.commit()
    async with maker() as s:
        got = await s.get(RequestRow, r1.id)
        assert got is not None
        assert got.user_id is None
        assert got.answer == answer
        assert Answer.model_validate(got.answer).claims[0].citations[0].segment_id == 1
        assert (got.tokens_cached, got.latency_ms, got.cached) == (1000, 950, False)
        assert got.cost_usd == Decimal("0.000321")

        rows = await queries.list_requests(s, EP)
        assert [r.id for r in rows] == [r2.id, r1.id]  # newest first
        cached = rows[0]
        assert (cached.cached, cached.tokens_in, cached.cost_usd) == (True, 0, Decimal("0"))
        assert cached.model is None and cached.error is None
        presets = await queries.list_requests(s, EP, feature="preset")
        assert [r.id for r in presets] == [r2.id]
        assert len(await queries.list_requests(s, EP, limit=1)) == 1


async def test_record_request_with_user_and_error(maker) -> None:
    async with maker() as s:
        r = await queries.record_request(
            s,
            episode_id=EP,
            feature="chat",
            question="Hi?",
            answer=None,
            prompt_version="chat-v1",
            error="model timeout",
            user_id="user_123",
        )
        await s.commit()
        assert r.user_id == "user_123" and r.error == "model timeout"


async def test_unknown_request_feature_rejected(maker) -> None:
    async with maker() as s:
        with pytest.raises(IntegrityError, match="ck_requests_feature"):
            await queries.record_request(
                s, episode_id=EP, feature="other", answer=None, prompt_version="v1"
            )


async def test_episode_delete_cascades(maker) -> None:
    async with maker() as s:
        await _save(s)
        await queries.record_request(
            s, episode_id=EP, feature="ask", answer=None, prompt_version="ask-v1"
        )
        await s.commit()
    async with maker() as s:
        await s.execute(delete(Episode).where(Episode.id == EP))
        await s.commit()
        assert await s.scalar(select(func.count()).select_from(ArtifactRow)) == 0
        assert await s.scalar(select(func.count()).select_from(RequestRow)) == 0
