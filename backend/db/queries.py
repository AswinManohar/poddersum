"""Bulk upserts and lookups: podcasts and episodes (0001); transcripts, segments, jobs
and job events (0002); artifacts and requests (0003).

Convention: helpers execute/flush but never commit; the caller owns the transaction."""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import (
    ACTIVE_JOB_STATES,
    ArtifactRow,
    Episode,
    Job,
    JobEventRow,
    Podcast,
    RequestRow,
    Segment,
    Transcript,
)
from discovery.feed import FeedEpisode, ParsedFeed
from discovery.itunes import ItunesPodcast

if TYPE_CHECKING:
    from ingest.segmenter import SegmentData
    from ingest.transcriber import TranscriptResult

# 13 bound parameters per episode row; asyncpg allows 32767 per statement.
UPSERT_CHUNK = 1000

_EPISODE_COLUMNS = (
    "podcast_id",
    "guid",
    "title",
    "description",
    "published_at",
    "duration_s",
    "audio_url",
    "audio_type",
    "artwork_url",
    "transcript_tags",
)


async def upsert_podcasts(session: AsyncSession, hits: Sequence[ItunesPodcast]) -> None:
    """Insert or refresh podcasts from iTunes. ``feed_url`` keeps its first recorded value
    for an id (ids never change); metadata is refreshed only when it differs."""
    if not hits:
        return
    rows = [
        {
            "id": h.id,
            "feed_url": h.feed_url,
            "itunes_id": h.itunes_id,
            "title": h.title,
            "author": h.author,
            "artwork_url": h.artwork_url,
            "genre": h.genre,
        }
        for h in hits
    ]
    stmt = insert(Podcast).values(rows)
    cols = ("itunes_id", "title", "author", "artwork_url", "genre")
    table = Podcast.__table__
    stmt = stmt.on_conflict_do_update(
        index_elements=[table.c.id],
        set_={**{c: stmt.excluded[c] for c in cols}, "updated_at": func.now()},
        where=or_(*[table.c[c].is_distinct_from(stmt.excluded[c]) for c in cols]),
    )
    await session.execute(stmt)


async def fill_podcast_from_feed(session: AsyncSession, podcast: Podcast, feed: ParsedFeed) -> None:
    """Fill gaps in podcast metadata from the feed channel (iTunes values win)."""
    changed = False
    if not podcast.author and feed.author:
        podcast.author = feed.author
        changed = True
    if not podcast.artwork_url and feed.artwork_url:
        podcast.artwork_url = feed.artwork_url
        changed = True
    if changed:
        await session.flush()


def _episode_row(podcast_id: str, ep: FeedEpisode) -> dict:
    return {
        "id": ep.id,
        "podcast_id": podcast_id,
        "guid": ep.guid,
        "title": ep.title,
        "description": ep.description,
        "published_at": ep.published_at,
        "duration_s": ep.duration_s,
        "audio_url": ep.audio_url,
        "audio_type": ep.audio_type,
        "artwork_url": ep.artwork_url,
        "transcript_tags": [t.as_json() for t in ep.transcript_tags],
    }


async def upsert_episodes(
    session: AsyncSession, podcast_id: str, episodes: Iterable[FeedEpisode]
) -> None:
    """Bulk upsert in chunks; rows whose values did not change are not rewritten."""
    rows = [_episode_row(podcast_id, ep) for ep in episodes]
    table = Episode.__table__
    for start in range(0, len(rows), UPSERT_CHUNK):
        stmt = insert(Episode).values(rows[start : start + UPSERT_CHUNK])
        stmt = stmt.on_conflict_do_update(
            index_elements=[table.c.id],
            set_={**{c: stmt.excluded[c] for c in _EPISODE_COLUMNS}, "updated_at": func.now()},
            where=or_(*[table.c[c].is_distinct_from(stmt.excluded[c]) for c in _EPISODE_COLUMNS]),
        )
        await session.execute(stmt)


async def get_podcast(session: AsyncSession, podcast_id: str) -> Podcast | None:
    return await session.get(Podcast, podcast_id)


async def get_episode_with_podcast(
    session: AsyncSession, episode_id: str
) -> tuple[Episode, Podcast] | None:
    row = (
        await session.execute(
            select(Episode, Podcast)
            .join(Podcast, Podcast.id == Episode.podcast_id)
            .where(Episode.id == episode_id)
        )
    ).first()
    return (row[0], row[1]) if row else None


# --------------------------------------------------------------------------- transcripts


async def get_transcript(session: AsyncSession, episode_id: str) -> Transcript | None:
    return (
        await session.execute(select(Transcript).where(Transcript.episode_id == episode_id))
    ).scalar_one_or_none()


async def has_transcript(session: AsyncSession, episode_id: str) -> bool:
    stmt = select(Transcript.id).where(Transcript.episode_id == episode_id).limit(1)
    return (await session.execute(stmt)).first() is not None


async def get_episode_segments(session: AsyncSession, episode_id: str) -> list[Segment]:
    """All segments of an episode in transcript order (``idx``). ``Segment.id`` is the
    ``Citation.segment_id``; empty list when the episode has no transcript yet."""
    stmt = select(Segment).where(Segment.episode_id == episode_id).order_by(Segment.idx)
    return list((await session.execute(stmt)).scalars())


SEGMENT_INSERT_CHUNK = 3000  # 7 params per row


async def store_transcript(
    session: AsyncSession,
    episode_id: str,
    result: TranscriptResult,
    segments: Sequence[SegmentData],
) -> Transcript:
    """Insert the transcript and its segments (caller commits: one transaction)."""
    transcript = Transcript(
        episode_id=episode_id,
        source=result.source,
        source_url=result.source_url,
        model=result.model,
        cost_usd=round(result.cost_usd, 6),
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
        language=result.language,
        duration_ms=result.duration_ms,
        raw=result.raw,
    )
    session.add(transcript)
    await session.flush()
    rows = [
        {
            "transcript_id": transcript.id,
            "episode_id": episode_id,
            "idx": seg.idx,
            "start_ms": seg.start_ms,
            "end_ms": seg.end_ms,
            "speaker": seg.speaker,
            "text": seg.text,
        }
        for seg in segments
    ]
    for start in range(0, len(rows), SEGMENT_INSERT_CHUNK):
        await session.execute(insert(Segment).values(rows[start : start + SEGMENT_INSERT_CHUNK]))
    return transcript


# --------------------------------------------------------------------------- jobs


async def get_job(session: AsyncSession, job_id: uuid.UUID) -> Job | None:
    return await session.get(Job, job_id, populate_existing=True)


async def get_active_job(session: AsyncSession, episode_id: str) -> Job | None:
    stmt = select(Job).where(
        Job.episode_id == episode_id, Job.kind == "ingest", Job.state.in_(ACTIVE_JOB_STATES)
    )
    return (
        await session.execute(stmt.execution_options(populate_existing=True))
    ).scalar_one_or_none()


async def get_latest_job(
    session: AsyncSession, episode_id: str, *, state: str | None = None
) -> Job | None:
    stmt = select(Job).where(Job.episode_id == episode_id, Job.kind == "ingest")
    if state is not None:
        stmt = stmt.where(Job.state == state)
    stmt = stmt.order_by(Job.created_at.desc()).limit(1)
    return (
        await session.execute(stmt.execution_options(populate_existing=True))
    ).scalar_one_or_none()


def event_payload(
    job_id: uuid.UUID,
    seq: int,
    state: str,
    step: str,
    progress: float,
    message: str | None,
    ts: datetime,
) -> dict[str, Any]:
    """JSON stored in ``job_events.payload``; the shape of ``api.schemas.JobEvent``."""
    return {
        "seq": seq,
        "job_id": str(job_id),
        "state": state,
        "step": step,
        "progress": progress,
        "message": message,
        "ts": ts.isoformat(),
    }


async def create_job(
    session: AsyncSession,
    episode_id: str,
    *,
    state: str = "queued",
    step: str = "queued",
    progress: float = 0.0,
    message: str | None = "Queued",
) -> Job | None:
    """Insert a job plus its first event (seq 1). Returns ``None`` when an active ingest
    job already exists for the episode (partial unique index). Caller commits."""
    attempts = (
        select(func.count() + 1).select_from(Job).where(Job.episode_id == episode_id)
    ).scalar_subquery()
    terminal = state not in ACTIVE_JOB_STATES
    stmt = (
        insert(Job)
        .values(
            id=uuid.uuid4(),
            episode_id=episode_id,
            kind="ingest",
            state=state,
            step=step,
            progress=progress,
            message=message,
            attempts=attempts,
            last_seq=1,
            finished_at=func.now() if terminal else None,
        )
        .on_conflict_do_nothing(
            index_elements=[Job.__table__.c.episode_id],
            index_where=text("kind = 'ingest' AND state IN ('queued', 'running')"),
        )
        .returning(Job.id)
    )
    job_id = (await session.execute(stmt)).scalar_one_or_none()
    if job_id is None:
        return None
    job = await get_job(session, job_id)
    assert job is not None
    session.add(
        JobEventRow(
            job_id=job.id,
            seq=1,
            payload=event_payload(job.id, 1, state, step, progress, message, job.updated_at),
            ts=job.updated_at,
        )
    )
    await session.flush()
    return job


async def append_job_event(
    session: AsyncSession,
    job_id: uuid.UUID,
    *,
    state: str,
    step: str,
    progress: float,
    message: str | None,
    error: str | None = None,
    require_active: bool = True,
) -> dict[str, Any] | None:
    """Update the job row and append the next numbered event, atomically (caller commits).

    With ``require_active`` the update only applies to a queued/running job; returns
    ``None`` when the job is no longer active (e.g. failed by restart recovery)."""
    terminal = state not in ACTIVE_JOB_STATES
    values: dict[str, Any] = {
        "state": state,
        "step": step,
        "progress": progress,
        "message": message,
        "last_seq": Job.last_seq + 1,
        "updated_at": func.clock_timestamp(),
    }
    if error is not None:
        values["error"] = error
    if terminal:
        values["finished_at"] = func.clock_timestamp()
    stmt = update(Job).where(Job.id == job_id)
    if require_active:
        stmt = stmt.where(Job.state.in_(ACTIVE_JOB_STATES))
    row = (
        await session.execute(
            stmt.values(**values)
            .returning(Job.last_seq, Job.updated_at)
            .execution_options(synchronize_session=False)
        )
    ).first()
    if row is None:
        return None
    seq, ts = row
    payload = event_payload(job_id, seq, state, step, progress, message, ts)
    await session.execute(
        insert(JobEventRow).values(job_id=job_id, seq=seq, payload=payload, ts=ts)
    )
    return payload


async def list_job_events(
    session: AsyncSession, job_id: uuid.UUID, *, after_seq: int = 0
) -> list[dict[str, Any]]:
    stmt = (
        select(JobEventRow.payload)
        .where(JobEventRow.job_id == job_id, JobEventRow.seq > after_seq)
        .order_by(JobEventRow.seq)
    )
    return list((await session.execute(stmt)).scalars())


async def list_active_jobs(session: AsyncSession) -> list[Job]:
    stmt = select(Job).where(Job.state.in_(ACTIVE_JOB_STATES)).order_by(Job.created_at)
    return list((await session.execute(stmt)).scalars())


# --------------------------------------------------------------------------- artifacts


def _usd(value: float | Decimal) -> Decimal:
    return round(Decimal(str(value)), 6)


async def get_artifact(
    session: AsyncSession, episode_id: str, kind: str, prompt_version: str
) -> ArtifactRow | None:
    """The cached preset for the cache key, or ``None`` (miss)."""
    stmt = select(ArtifactRow).where(
        ArtifactRow.episode_id == episode_id,
        ArtifactRow.kind == kind,
        ArtifactRow.prompt_version == prompt_version,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def save_artifact(
    session: AsyncSession,
    *,
    episode_id: str,
    kind: str,
    prompt_version: str,
    answer: dict[str, Any],
    model: str,
    tokens_in: int,
    tokens_out: int,
    cost_usd: float | Decimal,
) -> ArtifactRow:
    """Race-safe insert: ``ON CONFLICT (episode_id, kind, prompt_version) DO NOTHING`` then
    select, so concurrent generations end with one row and every caller gets the STORED
    one (which may be another caller's answer). Caller commits.

    Under READ COMMITTED a conflicting insert blocks until the other transaction commits
    or rolls back, so the following select sees the winner's row."""
    stmt = (
        insert(ArtifactRow)
        .values(
            episode_id=episode_id,
            kind=kind,
            prompt_version=prompt_version,
            answer=answer,
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=_usd(cost_usd),
        )
        .on_conflict_do_nothing(index_elements=["episode_id", "kind", "prompt_version"])
    )
    await session.execute(stmt)
    row = await get_artifact(session, episode_id, kind, prompt_version)
    assert row is not None
    return row


# --------------------------------------------------------------------------- requests


async def record_request(
    session: AsyncSession,
    *,
    episode_id: str,
    feature: str,
    kind: str | None = None,
    question: str | None = None,
    answer: dict[str, Any] | None,
    prompt_version: str,
    model: str | None = None,
    tokens_in: int = 0,
    tokens_out: int = 0,
    tokens_cached: int = 0,
    cost_usd: float | Decimal = 0,
    cached: bool = False,
    latency_ms: int | None = None,
    error: str | None = None,
    user_id: str | None = None,
) -> RequestRow:
    """Insert one ``requests`` row (``feature``: ask | preset | chat) and flush so ``id``
    and ``created_at`` are loaded. Caller commits."""
    row = RequestRow(
        user_id=user_id,
        episode_id=episode_id,
        feature=feature,
        kind=kind,
        question=question,
        answer=answer,
        prompt_version=prompt_version,
        model=model,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        tokens_cached=tokens_cached,
        cost_usd=_usd(cost_usd),
        cached=cached,
        latency_ms=latency_ms,
        error=error,
        # wall clock, not transaction start: keeps newest-first order within a transaction
        created_at=func.clock_timestamp(),
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return row


async def list_requests(
    session: AsyncSession, episode_id: str, *, feature: str | None = None, limit: int = 50
) -> list[RequestRow]:
    """Requests for an episode, newest first."""
    stmt = select(RequestRow).where(RequestRow.episode_id == episode_id)
    if feature is not None:
        stmt = stmt.where(RequestRow.feature == feature)
    stmt = stmt.order_by(RequestRow.created_at.desc(), RequestRow.id).limit(limit)
    return list((await session.execute(stmt)).scalars())


# --------------------------------------------------------------------------- status


@dataclass(frozen=True, slots=True)
class EpisodeProcessing:
    status: str  # api.schemas.EpisodeStatus value
    active_job_id: str | None = None


_STATUS_SQL = text(
    """
    SELECT e.id,
           EXISTS (SELECT 1 FROM transcripts t WHERE t.episode_id = e.id) AS has_transcript,
           (SELECT j.id FROM jobs j
             WHERE j.episode_id = e.id AND j.kind = 'ingest'
               AND j.state IN ('queued', 'running')
             LIMIT 1) AS active_job_id,
           (SELECT j.state FROM jobs j
             WHERE j.episode_id = e.id AND j.kind = 'ingest'
             ORDER BY j.created_at DESC LIMIT 1) AS latest_state
      FROM unnest(CAST(:ids AS text[])) AS e(id)
    """
)


async def episode_processing(
    session: AsyncSession, episode_ids: Sequence[str]
) -> dict[str, EpisodeProcessing]:
    """Status for many episodes in ONE query: ``ready`` (transcript stored) >
    ``processing`` (active job) > ``failed`` (latest job failed) > ``new``."""
    if not episode_ids:
        return {}
    rows = (await session.execute(_STATUS_SQL, {"ids": list(episode_ids)})).all()
    out: dict[str, EpisodeProcessing] = {}
    for ep_id, has_t, active_id, latest in rows:
        if has_t:
            out[ep_id] = EpisodeProcessing("ready")
        elif active_id is not None:
            out[ep_id] = EpisodeProcessing("processing", str(active_id))
        elif latest == "failed":
            out[ep_id] = EpisodeProcessing("failed")
        else:
            out[ep_id] = EpisodeProcessing("new")
    return out
