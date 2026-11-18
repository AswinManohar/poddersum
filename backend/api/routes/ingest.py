"""``POST /episodes/{episode_id}/ingest``: start (or join) transcript ingestion."""

from __future__ import annotations

from fastapi import APIRouter, Request

from api.deps import ApiError, errors
from api.routes.common import EpisodeIdPath
from api.schemas import JobStatus
from ingest.runner import IngestRunner, UnknownEpisodeError, enqueue_ingest

router = APIRouter(tags=["jobs"])


def get_ingest_runner(request: Request) -> IngestRunner:
    return request.app.state.ingest_runner


@router.post(
    "/episodes/{episode_id}/ingest",
    status_code=202,
    response_model=JobStatus,
    responses=errors(404),
    summary="Start (or join) transcript ingestion for an episode",
)
async def start_ingest(episode_id: EpisodeIdPath, request: Request) -> JobStatus:
    """Returns at once with the job; progress streams from ``GET /jobs/{job_id}/events``.
    Idempotent per episode: an existing queued/running job (or a finished one for a ready
    episode) is returned instead of starting a second transcription. After a failed job a
    new attempt is started."""
    try:
        return await enqueue_ingest(episode_id, runner=get_ingest_runner(request))
    except UnknownEpisodeError:
        raise ApiError(404, "not_found", "Unknown episode.") from None
