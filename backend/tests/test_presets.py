"""Preset cache (agent.presets): miss, hit, prompt version bump, concurrency, verdict order."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent import presets
from agent.deps import AgentDeps
from agent.errors import AgentError
from agent.prompts import Prompt
from agent.transcript_context import SegmentRef
from api.schemas import ArtifactKind
from db.models import ArtifactRow, RequestRow
from llm.fake import FakeReply
from tests.conftest import PG_SKIP_REASON, PG_UP
from tests.fakes import ANSWER_EPISODE_ID, fake_answer_gateway, seed_answer_episode

pytestmark = [pytest.mark.db, pytest.mark.skipif(not PG_UP, reason=PG_SKIP_REASON)]


def claims_json(segs: Sequence[SegmentRef], *extra: dict) -> str:
    claims = [
        {"text": "The episode is about pricing.", "segment_ids": [segs[0].id]},
        {"text": "Charge more early.", "segment_ids": [segs[1].id, 999_999]},
        {"text": "Invented.", "segment_ids": [999_999]},
        *extra,
    ]
    return json.dumps({"claims": claims})


@pytest.fixture
async def segs(db_sessionmaker: async_sessionmaker[AsyncSession]) -> list[SegmentRef]:
    return await seed_answer_episode(db_sessionmaker)


async def rows(maker: async_sessionmaker[AsyncSession], model: type) -> list:
    async with maker() as s:
        stmt = select(model)
        if model is RequestRow:
            stmt = stmt.order_by(RequestRow.created_at)
        return list((await s.scalars(stmt)).all())


async def test_miss_then_hit(
    db_sessionmaker: async_sessionmaker[AsyncSession], segs: list[SegmentRef]
) -> None:
    reply = FakeReply(claims_json(segs), tokens_in=1200, tokens_out=90, cached=0)
    gateway, provider = fake_answer_gateway([reply])
    deps = AgentDeps(db_sessionmaker, gateway)

    first = await presets.get_or_create_artifact(deps, ANSWER_EPISODE_ID, ArtifactKind.tldr)
    assert len(provider.calls) == 1
    assert first.answer.cached is False
    assert first.prompt_version == "tldr-v1+system-v1"
    assert [c.text for c in first.answer.claims] == [
        "The episode is about pricing.",
        "Charge more early.",
    ]
    (artifact,) = await rows(db_sessionmaker, ArtifactRow)
    assert (artifact.kind, artifact.prompt_version) == ("tldr", "tldr-v1+system-v1")
    (req,) = await rows(db_sessionmaker, RequestRow)
    assert (req.feature, req.kind, req.cached, req.tokens_in) == ("preset", "tldr", False, 1200)
    # text only, no audio/file parts
    assert all(isinstance(m["content"], str) for m in provider.calls[0]["messages"])

    second = await presets.get_or_create_artifact(deps, ANSWER_EPISODE_ID, ArtifactKind.tldr)
    assert len(provider.calls) == 1  # zero model calls on a hit
    assert second.answer.cached is True
    assert second.answer.claims == first.answer.claims
    assert second.answer.tokens_in == 1200  # tokens of the original call, kept
    reqs = await rows(db_sessionmaker, RequestRow)
    assert len(reqs) == 2
    hit = reqs[1]
    assert (hit.cached, hit.tokens_in, hit.tokens_out, float(hit.cost_usd)) == (True, 0, 0, 0.0)
    assert len(await rows(db_sessionmaker, ArtifactRow)) == 1


async def test_prompt_version_bump_regenerates_only_that_kind(
    db_sessionmaker: async_sessionmaker[AsyncSession],
    segs: list[SegmentRef],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway, provider = fake_answer_gateway([claims_json(segs)])
    deps = AgentDeps(db_sessionmaker, gateway)
    for kind in (ArtifactKind.tldr, ArtifactKind.takeaways):
        await presets.get_or_create_artifact(deps, ANSWER_EPISODE_ID, kind)
    assert len(provider.calls) == 2

    old = presets.PRESETS[ArtifactKind.tldr]
    bumped = Prompt(name="tldr", version="tldr-v2", text=old.prompt.text + "Be brief.\n")
    monkeypatch.setitem(
        presets.PRESETS, ArtifactKind.tldr, presets.PresetSpec(bumped, old.output_schema)
    )

    tldr = await presets.get_or_create_artifact(deps, ANSWER_EPISODE_ID, ArtifactKind.tldr)
    assert len(provider.calls) == 3
    assert tldr.answer.cached is False and tldr.prompt_version == "tldr-v2+system-v1"
    takeaways = await presets.get_or_create_artifact(
        deps, ANSWER_EPISODE_ID, ArtifactKind.takeaways
    )
    assert len(provider.calls) == 3 and takeaways.answer.cached is True
    versions = sorted(a.prompt_version for a in await rows(db_sessionmaker, ArtifactRow))
    assert versions == ["takeaways-v1+system-v1", "tldr-v1+system-v1", "tldr-v2+system-v1"]


async def test_concurrent_misses_make_one_model_call(
    db_sessionmaker: async_sessionmaker[AsyncSession], segs: list[SegmentRef]
) -> None:
    gateway, provider = fake_answer_gateway([claims_json(segs)])
    deps = AgentDeps(db_sessionmaker, gateway)
    results = await asyncio.gather(
        *(
            presets.get_or_create_artifact(deps, ANSWER_EPISODE_ID, ArtifactKind.tldr)
            for _ in range(4)
        )
    )
    assert len(provider.calls) == 1
    assert sorted(r.answer.cached for r in results) == [False, True, True, True]
    assert len({tuple(c.text for c in r.answer.claims) for r in results}) == 1
    assert len(await rows(db_sessionmaker, ArtifactRow)) == 1


async def test_verdict_claims_ordered_by_section_and_sections_required(
    db_sessionmaker: async_sessionmaker[AsyncSession], segs: list[SegmentRef]
) -> None:
    claims = [
        {
            "section": "best_10_minutes",
            "text": "Minutes 1-3.",
            "segment_ids": [segs[1].id, segs[3].id],
        },
        {"section": "whats_new", "text": "High price converted.", "segment_ids": [segs[2].id]},
        {"section": "who_its_for", "text": "Founders.", "segment_ids": [segs[0].id]},
        {"section": "whats_new", "text": "Hire late.", "segment_ids": [segs[3].id]},
        {"section": "who_its_for", "text": "Bad id.", "segment_ids": [999_999]},
    ]
    gateway, _ = fake_answer_gateway([json.dumps({"claims": claims})])
    artifact = await presets.get_or_create_artifact(
        AgentDeps(db_sessionmaker, gateway), ANSWER_EPISODE_ID, ArtifactKind.verdict
    )
    got = [(c.section, c.text) for c in artifact.answer.claims]
    assert got == [
        ("who_its_for", "Founders."),
        ("whats_new", "High price converted."),
        ("whats_new", "Hire late."),
        ("best_10_minutes", "Minutes 1-3."),
    ]


async def test_zero_grounded_claims_raises_and_caches_nothing(
    db_sessionmaker: async_sessionmaker[AsyncSession], segs: list[SegmentRef]
) -> None:
    reply = json.dumps({"claims": [{"text": "Invented.", "segment_ids": [999_999]}]})
    gateway, provider = fake_answer_gateway([reply])
    deps = AgentDeps(db_sessionmaker, gateway)
    with pytest.raises(AgentError) as info:
        await presets.get_or_create_artifact(deps, ANSWER_EPISODE_ID, ArtifactKind.tldr)
    assert info.value.code == "no_grounded_claims"
    assert await rows(db_sessionmaker, ArtifactRow) == []
    (req,) = await rows(db_sessionmaker, RequestRow)
    assert req.error and req.answer is None and req.tokens_in > 0
