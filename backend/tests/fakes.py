"""Shared fakes: a gateway on FakeProvider with a fake model registry (no network)."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent.transcript_context import SegmentRef
from core.settings import Settings
from core.tracing import Tracer
from db import queries
from llm import Gateway
from llm.config import ModelsConfig
from llm.fake import FakeProvider, FakeReply

FAKE_MODEL = "fake/fake-stt-1"


def fake_config() -> ModelsConfig:
    return ModelsConfig.model_validate(
        {
            "tasks": {"stt_cheap": {"model": "lite", "params": {}, "max_retries": 0}},
            "models": {
                "lite": {
                    "litellm_model": FAKE_MODEL,
                    "provider": "gemini",
                    "prices_per_1m_usd": {
                        "input_text": 0.25,
                        "input_audio": 0.50,
                        "output": 1.50,
                        "cached_input_text": 0.025,
                        "cached_input_audio": 0.05,
                    },
                }
            },
        }
    )


def _no_tracing(_: Settings) -> None:
    raise AssertionError("tracing client must not be constructed in tests")


async def _no_sleep(_: float) -> None:
    return None


def fake_gateway(
    script: Iterable[FakeReply | str | BaseException],
) -> tuple[Gateway, FakeProvider]:
    settings = Settings(_env_file=None, GEMINI_API_KEY="test-dummy-key")
    provider = FakeProvider(script)
    gateway = Gateway(
        settings=settings,
        config=fake_config(),
        provider=provider,
        tracer=Tracer(settings, client_factory=_no_tracing),
        sleep=_no_sleep,
    )
    return gateway, provider


# --- answer engine (M0-S5) ------------------------------------------------------

FAKE_ANSWER_MODEL = "fake/fake-answer-1"
FAKE_ANSWER_MODEL_ID = FAKE_ANSWER_MODEL.split("/", 1)[1]  # what LLMResult.model reports
FAKE_CONTEXT_WINDOW = 100_000


def fake_answer_config(context_window: int = FAKE_CONTEXT_WINDOW) -> ModelsConfig:
    """Registry with the ``summary`` (ask + presets) and ``chat`` tasks at the real yaml's
    prices."""
    return ModelsConfig.model_validate(
        {
            "tasks": {
                "summary": {"model": "lite", "params": {"max_tokens": 1000}, "max_retries": 0},
                "chat": {"model": "lite", "params": {"max_tokens": 500}, "max_retries": 0},
            },
            "models": {
                "lite": {
                    "litellm_model": FAKE_ANSWER_MODEL,
                    "provider": "gemini",
                    "context_window": context_window,
                    "prices_per_1m_usd": {
                        "input_text": 0.25,
                        "input_audio": 0.50,
                        "output": 1.50,
                        "cached_input_text": 0.025,
                        "cached_input_audio": 0.05,
                    },
                }
            },
        }
    )


def fake_answer_gateway(
    script: Iterable[FakeReply | str | BaseException],
    *,
    context_window: int = FAKE_CONTEXT_WINDOW,
    provider: FakeProvider | None = None,
) -> tuple[Gateway, FakeProvider]:
    """Gateway for ``agent/`` tests; pass ``provider`` to use a custom FakeProvider."""
    settings = Settings(_env_file=None, GEMINI_API_KEY="test-dummy-key")
    provider = provider or FakeProvider(script)
    gateway = Gateway(
        settings=settings,
        config=fake_answer_config(context_window),
        provider=provider,
        tracer=Tracer(settings, client_factory=_no_tracing),
        sleep=_no_sleep,
    )
    return gateway, provider


# --- seeded episode for answer-engine tests (M0-S5) ---------------------------------

ANSWER_PODCAST_ID = "c" * 64
ANSWER_EPISODE_ID = "d" * 64
BARE_EPISODE_ID = "e" * 64  # exists, but has no transcript
ANSWER_TRANSCRIPT_FIXTURE = Path(__file__).parent / "fixtures" / "answer_transcript.json"


async def seed_answer_episode(maker: async_sessionmaker[AsyncSession]) -> list[SegmentRef]:
    """Store the fixture transcript for ``ANSWER_EPISODE_ID`` (plus ``BARE_EPISODE_ID``
    without a transcript) and return its segments with their database ids."""
    from db.models import Episode, Podcast, Segment, Transcript

    data = json.loads(ANSWER_TRANSCRIPT_FIXTURE.read_text(encoding="utf-8"))
    async with maker() as s:
        s.add(Podcast(id=ANSWER_PODCAST_ID, feed_url="https://feed.example/a", title="Pod"))
        await s.flush()
        for ep_id in (ANSWER_EPISODE_ID, BARE_EPISODE_ID):
            s.add(
                Episode(
                    id=ep_id,
                    podcast_id=ANSWER_PODCAST_ID,
                    guid=ep_id[:8],
                    title="Episode",
                    audio_url=f"https://audio.example/{ep_id[:8]}.mp3",
                )
            )
        await s.flush()
        transcript = Transcript(episode_id=ANSWER_EPISODE_ID, source="feed")
        s.add(transcript)
        await s.flush()
        s.add_all(
            Segment(transcript_id=transcript.id, episode_id=ANSWER_EPISODE_ID, idx=i, **seg)
            for i, seg in enumerate(data["segments"])
        )
        await s.commit()
        rows = await queries.get_episode_segments(s, ANSWER_EPISODE_ID)
        return [SegmentRef.from_row(r) for r in rows]
