"""In-process ingest runner behind :func:`enqueue_ingest` (Inngest replaces it in P2-S1).

Job lifecycle
-------------
``enqueue_ingest(episode_id)`` is the single entry point. It never does slow work: it
returns a :class:`~api.schemas.JobStatus` at once and starts an asyncio task.

* episode already has a transcript -> its latest succeeded job (no new work);
* a queued/running job exists -> that job;
* otherwise (never ingested, or the last job failed) -> a new job (``attempts`` + 1).

At most one queued/running ingest job per episode is guaranteed by the partial unique
index ``uq_jobs_active_ingest``; the task additionally holds a Postgres advisory lock on
the episode (``ingest/locks.py``) for its whole life.

Steps (``JobStep``), each persisted as a numbered ``job_events`` row that SSE replays:
``queued -> fetch_transcript -> [download_audio -> transcribe] -> segment -> store -> done``.
The step functions (:func:`acquire_transcript`, :func:`segment_transcript`,
:func:`store_segments`) are plain coroutines/functions so a durable workflow can call them
one by one. The transcript, its segments and the final ``succeeded`` event are written in
ONE transaction, so "has a transcript" and "job succeeded" never disagree.

Restarts: on API startup :meth:`IngestRunner.recover_interrupted` marks every job left
``queued``/``running`` by a dead process as ``failed`` ("interrupted by restart"), with a
final event so open streams close; ``POST /episodes/{id}/ingest`` then starts a fresh
attempt. Jobs whose episode lock is still held by a live process are left alone. On a clean
shutdown running jobs are cancelled and marked failed the same way.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from api.schemas import JobState, JobStatus, JobStep
from db import queries
from db.models import Episode, Job
from ingest.events import JobNotifier
from ingest.feed_transcript import fetch_feed_transcript
from ingest.locks import LockNotAcquired, episode_lock, lock_is_free
from ingest.segmenter import SegmentData, segment_cues
from ingest.transcriber import (
    AudioSource,
    Reporter,
    Transcriber,
    TranscriptionError,
    TranscriptResult,
    no_report,
)
from llm import LLMError

log = logging.getLogger(__name__)

ERROR_MAX_CHARS = 300
INTERRUPTED_BY_RESTART = "interrupted by restart"
INTERRUPTED_BY_SHUTDOWN = "interrupted by shutdown"

# Overall progress band of each step.
PROGRESS = {
    JobStep.queued: (0.0, 0.0),
    JobStep.fetch_transcript: (0.02, 0.10),
    JobStep.download_audio: (0.10, 0.20),
    JobStep.transcribe: (0.20, 0.85),
    JobStep.segment: (0.88, 0.88),
    JobStep.store: (0.94, 0.94),
    JobStep.done: (1.0, 1.0),
}
MIN_PROGRESS_DELTA = 0.02  # message-less progress events are throttled to this step


class UnknownEpisodeError(LookupError):
    pass


class JobAbortedError(RuntimeError):
    """The job row is no longer active (e.g. failed by restart recovery)."""


def job_status(job: Job) -> JobStatus:
    return JobStatus(
        job_id=str(job.id),
        episode_id=job.episode_id,
        state=JobState(job.state),
        step=JobStep(job.step),
        progress=min(max(job.progress, 0.0), 1.0),
        message=job.message,
        error=job.error,
        created_at=job.created_at,
        updated_at=job.updated_at,
        finished_at=job.finished_at,
    )


def safe_error(exc: BaseException) -> str:
    """Short, user-safe failure reason (gateway errors are already secret-free)."""
    if isinstance(exc, TranscriptionError | LLMError):
        msg = str(exc) or type(exc).__name__
    elif isinstance(exc, LockNotAcquired):
        msg = "another worker is already processing this episode"
    else:
        msg = f"internal error ({type(exc).__name__})"
    return msg[:ERROR_MAX_CHARS]


# --------------------------------------------------------------------------- step functions


async def acquire_transcript(
    http: httpx.AsyncClient,
    episode: Episode,
    transcriber_factory: Callable[[], Transcriber],
    report: Reporter = no_report,
    podcast_title: str | None = None,
) -> TranscriptResult:
    """Steps ``fetch_transcript`` then, when the feed has no usable timed transcript,
    ``download_audio`` + ``transcribe``."""
    await report(JobStep.fetch_transcript, 0.0, "Looking for a published transcript")
    tags = episode.transcript_tags or []
    result = await fetch_feed_transcript(http, tags) if tags else None
    if result is not None:
        fmt = str(result.raw.get("format", "")).upper()
        await report(JobStep.fetch_transcript, 1.0, f"Using the feed's {fmt} transcript")
        return result
    await report(
        JobStep.fetch_transcript,
        1.0,
        "No timed transcript in the feed; transcribing the audio",
    )
    transcriber = transcriber_factory()
    audio = AudioSource(
        episode_id=episode.id,
        audio_url=episode.audio_url,
        title=episode.title,
        podcast_title=podcast_title,
        duration_s=episode.duration_s,
    )
    return await transcriber.transcribe(audio, report)


def segment_transcript(result: TranscriptResult) -> list[SegmentData]:
    """Step ``segment``."""
    segments = segment_cues(result.cues)
    if not segments:
        raise TranscriptionError("the transcript contains no text")
    return segments


async def store_segments(
    session: AsyncSession,
    episode_id: str,
    result: TranscriptResult,
    segments: Sequence[SegmentData],
) -> int:
    """Step ``store`` (no commit): returns the transcript id."""
    transcript = await queries.store_transcript(session, episode_id, result, segments)
    return transcript.id


# --------------------------------------------------------------------------- runner


@dataclass
class _Progress:
    step: JobStep = JobStep.queued
    progress: float = 0.0
    message: str | None = None


class IngestRunner:
    """Runs ingest jobs as asyncio tasks in this process.

    Args:
        sessionmaker: bound to the app engine (its engine also serves the lock connection).
        http: client for feed transcript files.
        transcriber_factory: builds the STT :class:`Transcriber` (called only when needed).
        notifier: wakes SSE streams in this process (correctness comes from Postgres).
        lock_wait_s: how long a job waits for the episode lock before failing.
    """

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        http: httpx.AsyncClient,
        transcriber_factory: Callable[[], Transcriber],
        *,
        notifier: JobNotifier | None = None,
        lock_wait_s: float = 10.0,
    ) -> None:
        self.sessionmaker = sessionmaker
        self.http = http
        self.transcriber_factory = transcriber_factory
        self.notifier = notifier or JobNotifier()
        self.lock_wait_s = lock_wait_s
        self.tasks: dict[uuid.UUID, asyncio.Task[None]] = {}

    @property
    def engine(self) -> AsyncEngine:
        bind = self.sessionmaker.kw.get("bind")
        if not isinstance(bind, AsyncEngine):
            raise RuntimeError("sessionmaker must be bound to an AsyncEngine")
        return bind

    # -- enqueue -----------------------------------------------------------------------

    async def enqueue(self, episode_id: str) -> JobStatus:
        """See module docs. Raises :class:`UnknownEpisodeError`."""
        for _ in range(3):
            async with self.sessionmaker() as s:
                if await s.get(Episode, episode_id) is None:
                    raise UnknownEpisodeError(episode_id)
                if await queries.has_transcript(s, episode_id):
                    job = await queries.get_latest_job(s, episode_id, state="succeeded")
                    if job is None:  # transcript stored without a job (e.g. seeded)
                        job = await queries.create_job(
                            s,
                            episode_id,
                            state="succeeded",
                            step="done",
                            progress=1.0,
                            message="Transcript already stored",
                        )
                        await s.commit()
                    assert job is not None
                    return job_status(job)
                active = await queries.get_active_job(s, episode_id)
                if active is not None:
                    return job_status(active)
                job = await queries.create_job(s, episode_id)
                if job is None:  # lost the race to a concurrent request
                    await s.rollback()
                    continue
                await s.commit()
                status = job_status(job)
            self._spawn(job.id, episode_id)
            return status
        # Three lost races in a row: an active job must exist now.
        async with self.sessionmaker() as s:
            active = await queries.get_active_job(s, episode_id)
            if active is None:
                raise RuntimeError("could not create or find an ingest job")
            return job_status(active)

    def _spawn(self, job_id: uuid.UUID, episode_id: str) -> None:
        task = asyncio.create_task(self.run_job(job_id, episode_id), name=f"ingest-{job_id}")
        self.tasks[job_id] = task
        task.add_done_callback(lambda _t: self.tasks.pop(job_id, None))

    # -- events ------------------------------------------------------------------------

    async def _emit(
        self,
        job_id: uuid.UUID,
        state: JobState,
        step: JobStep,
        progress: float,
        message: str | None,
        *,
        error: str | None = None,
        session: AsyncSession | None = None,
    ) -> dict[str, Any]:
        async def write(s: AsyncSession) -> dict[str, Any] | None:
            return await queries.append_job_event(
                s,
                job_id,
                state=state.value,
                step=step.value,
                progress=round(min(max(progress, 0.0), 1.0), 4),
                message=message,
                error=error,
            )

        if session is not None:
            payload = await write(session)
        else:
            async with self.sessionmaker() as s:
                payload = await write(s)
                await s.commit()
        if payload is None:
            raise JobAbortedError(str(job_id))
        if session is None:
            self.notifier.notify(str(job_id))
        return payload

    def _reporter(self, job_id: uuid.UUID, cur: _Progress) -> Reporter:
        async def report(step: JobStep, fraction: float, message: str | None) -> None:
            lo, hi = PROGRESS[step]
            p = max(cur.progress, lo + (hi - lo) * min(max(fraction, 0.0), 1.0))
            if message is None and step == cur.step and p - cur.progress < MIN_PROGRESS_DELTA:
                return
            cur.step, cur.progress = step, p
            if message is not None:
                cur.message = message
            await self._emit(job_id, JobState.running, step, p, cur.message)

        return report

    # -- the job -----------------------------------------------------------------------

    async def run_job(self, job_id: uuid.UUID, episode_id: str) -> None:
        cur = _Progress()
        try:
            async with episode_lock(self.engine, episode_id, wait_s=self.lock_wait_s):
                await self._run_locked(job_id, episode_id, cur)
        except JobAbortedError:
            log.info("ingest job %s is no longer active; stopped", job_id)
        except asyncio.CancelledError:
            await self._fail_quietly(job_id, cur, INTERRUPTED_BY_SHUTDOWN)
            raise
        except Exception as exc:
            if isinstance(exc, TranscriptionError | LLMError | LockNotAcquired):
                log.warning("ingest job %s failed: %s", job_id, safe_error(exc))
            else:
                log.exception("ingest job %s crashed", job_id)
            await self._fail_quietly(job_id, cur, safe_error(exc))

    async def _run_locked(self, job_id: uuid.UUID, episode_id: str, cur: _Progress) -> None:
        report = self._reporter(job_id, cur)
        async with self.sessionmaker() as s:
            found = await queries.get_episode_with_podcast(s, episode_id)
            already = await queries.has_transcript(s, episode_id)
        if found is None:
            raise TranscriptionError("episode no longer exists")
        if already:  # stored by an earlier job between enqueue and lock
            await self._emit(job_id, JobState.succeeded, JobStep.done, 1.0, "Transcript ready")
            return
        episode, podcast = found
        podcast_title = podcast.title

        result = await acquire_transcript(
            self.http, episode, self.transcriber_factory, report, podcast_title
        )
        await report(JobStep.segment, 0.0, "Splitting the transcript into segments")
        segments = segment_transcript(result)
        await report(JobStep.store, 0.0, f"Storing {len(segments)} segments")
        async with self.sessionmaker() as s:
            await store_segments(s, episode_id, result, segments)
            await self._emit(
                job_id,
                JobState.succeeded,
                JobStep.done,
                1.0,
                f"Transcript ready: {len(segments)} segments ({result.source})",
                session=s,
            )
            await s.commit()
        self.notifier.notify(str(job_id))
        log.info(
            "ingest job %s done: source=%s segments=%d cost=$%.4f",
            job_id,
            result.source,
            len(segments),
            result.cost_usd,
        )

    async def _fail_quietly(self, job_id: uuid.UUID, cur: _Progress, error: str) -> None:
        async def fail() -> None:
            await self._emit(job_id, JobState.failed, cur.step, cur.progress, error, error=error)

        try:
            async with asyncio.timeout(5):
                await asyncio.shield(fail())
        except JobAbortedError:
            pass
        except Exception:  # noqa: BLE001 - never let failure bookkeeping crash the task
            log.exception("could not mark ingest job %s failed", job_id)

    # -- lifecycle ---------------------------------------------------------------------

    async def recover_interrupted(self) -> int:
        """Fail jobs left queued/running by a dead process; returns how many."""
        async with self.sessionmaker() as s:
            jobs = await queries.list_active_jobs(s)
        failed = 0
        for job in jobs:
            if job.id in self.tasks or not await lock_is_free(self.engine, job.episode_id):
                continue
            try:
                await self._emit(
                    job.id,
                    JobState.failed,
                    JobStep(job.step),
                    job.progress,
                    INTERRUPTED_BY_RESTART,
                    error=INTERRUPTED_BY_RESTART,
                )
                failed += 1
            except JobAbortedError:
                continue
        if failed:
            log.warning("marked %d interrupted ingest job(s) failed", failed)
        return failed

    async def shutdown(self, timeout_s: float = 10.0) -> None:
        tasks = list(self.tasks.values())
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.wait(tasks, timeout=timeout_s)


# --------------------------------------------------------------------------- entry point

_default_runner: IngestRunner | None = None


def install_runner(runner: IngestRunner | None) -> None:
    """Set the process-wide runner (the API lifespan does this)."""
    global _default_runner
    _default_runner = runner


def get_runner() -> IngestRunner:
    if _default_runner is None:
        raise RuntimeError("ingest runner not started (API lifespan not running?)")
    return _default_runner


async def enqueue_ingest(episode_id: str, *, runner: IngestRunner | None = None) -> JobStatus:
    """THE entry point for ingestion. Returns at once; work runs in the background."""
    return await (runner or get_runner()).enqueue(episode_id)


def default_transcriber_factory(
    http: httpx.AsyncClient, gateway_factory: Callable[[], Any] | None = None
) -> Callable[[], Transcriber]:
    """Production STT: :class:`GeminiTranscriber` on ``llm.get_gateway()``."""

    def build() -> Transcriber:
        from ingest.gemini_transcriber import GeminiTranscriber
        from llm import get_gateway

        return GeminiTranscriber((gateway_factory or get_gateway)(), http)

    return build


__all__ = [
    "IngestRunner",
    "JobAbortedError",
    "UnknownEpisodeError",
    "acquire_transcript",
    "default_transcriber_factory",
    "enqueue_ingest",
    "get_runner",
    "install_runner",
    "job_status",
    "safe_error",
    "segment_transcript",
    "store_segments",
]
