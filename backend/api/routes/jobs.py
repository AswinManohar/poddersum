"""``GET /jobs/{job_id}`` and ``GET /jobs/{job_id}/events`` (SSE).

The event stream reads ``job_events`` from Postgres, so it replays correctly after an API
restart and for clients that connect after the job finished. It polls the table every
``POLL_INTERVAL_S`` and is woken early by the in-process notifier when the runner writes.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sse_starlette import EventSourceResponse

from api.deps import ApiError, SessionDep, errors
from api.openapi import sse_doc
from api.routes.common import JobIdPath
from api.schemas import JOB_STREAM_EVENT_NAME, JobEvent, JobState, JobStatus
from db import queries
from ingest.events import JobNotifier
from ingest.runner import job_status

router = APIRouter(tags=["jobs"])

POLL_INTERVAL_S = 1.0
PING_INTERVAL_S = 15
TERMINAL = {JobState.succeeded.value, JobState.failed.value}


def _job_uuid(job_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(job_id)
    except ValueError:
        raise ApiError(404, "not_found", "Unknown job.") from None


def _last_seq(value: str | None) -> int:
    try:
        return max(0, int(value)) if value else 0
    except ValueError:
        return 0


@router.get(
    "/jobs/{job_id}",
    response_model=JobStatus,
    responses=errors(404),
    summary="Get an ingest job's status",
)
async def get_job(job_id: JobIdPath, session: SessionDep) -> JobStatus:
    job = await queries.get_job(session, _job_uuid(job_id))
    if job is None:
        raise ApiError(404, "not_found", "Unknown job.")
    return job_status(job)


async def stream_job_events(
    maker: async_sessionmaker[AsyncSession],
    notifier: JobNotifier | None,
    job_id: uuid.UUID,
    after_seq: int,
    *,
    poll_s: float = POLL_INTERVAL_S,
) -> AsyncIterator[dict[str, str]]:
    """Stored events after ``after_seq``, then live ones, until the terminal event."""
    last = after_seq
    while True:
        async with maker() as s:
            payloads = await queries.list_job_events(s, job_id, after_seq=last)
            job = await queries.get_job(s, job_id) if not payloads else None
        for payload in payloads:
            event = JobEvent.model_validate(payload)
            last = event.seq
            yield {
                "event": JOB_STREAM_EVENT_NAME,
                "id": str(event.seq),
                "data": json.dumps(event.model_dump(mode="json")),
            }
            if event.state.value in TERMINAL:
                return
        # Resumed after the terminal event (or the job vanished): nothing more will come.
        if not payloads and (job is None or (job.state in TERMINAL and job.last_seq <= last)):
            return
        if notifier is not None:
            await notifier.wait(str(job_id), poll_s)
        else:
            await asyncio.sleep(poll_s)


@router.get(
    "/jobs/{job_id}/events",
    responses=errors(404),
    openapi_extra=sse_doc(
        "JobEvent",
        events=[JOB_STREAM_EVENT_NAME],
        description="SSE stream. Each frame: `event: job`, `id: <seq>`, "
        "`data: <JobEvent JSON>`. Replays events after `Last-Event-ID`; closes after "
        "a `succeeded` or `failed` event.",
    ),
    summary="Stream an ingest job's progress (SSE)",
)
async def job_events(
    job_id: JobIdPath,
    request: Request,
    session: SessionDep,
    last_event_id: Annotated[
        str | None,
        Header(alias="Last-Event-ID", description="Resume after this `seq`."),
    ] = None,
) -> EventSourceResponse:
    jid = _job_uuid(job_id)
    if await queries.get_job(session, jid) is None:
        raise ApiError(404, "not_found", "Unknown job.")
    await session.close()  # do not hold a pooled connection for the stream's lifetime
    runner = getattr(request.app.state, "ingest_runner", None)
    return EventSourceResponse(
        stream_job_events(
            runner.sessionmaker if runner else request.app.state.sessionmaker,
            runner.notifier if runner else None,
            jid,
            _last_seq(last_event_id),
        ),
        ping=PING_INTERVAL_S,
    )
