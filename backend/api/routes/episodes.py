"""``GET /episodes/{episode_id}``: stored episode metadata and processing status."""

from __future__ import annotations

from fastapi import APIRouter

from api.deps import ApiError, SessionDep, errors
from api.routes.common import EpisodeIdPath
from api.routes.podcasts import podcast_hit
from api.schemas import EpisodeDetail, EpisodeStatus, TranscriptTag
from db.queries import episode_processing, get_episode_with_podcast

router = APIRouter(tags=["episodes"])


@router.get(
    "/episodes/{episode_id}",
    response_model=EpisodeDetail,
    responses=errors(404),
    summary="Get an episode",
)
async def get_episode(episode_id: EpisodeIdPath, session: SessionDep) -> EpisodeDetail:
    """Episode metadata from the database (populated by ``/podcasts/{id}/episodes``),
    with its transcript ``status`` and ``active_job_id`` while processing."""
    found = await get_episode_with_podcast(session, episode_id)
    if found is None:
        raise ApiError(404, "not_found", "Unknown episode.")
    ep, podcast = found
    proc = (await episode_processing(session, [ep.id]))[ep.id]
    tags = [TranscriptTag.model_validate(t) for t in ep.transcript_tags or []]
    return EpisodeDetail(
        id=ep.id,
        podcast_id=ep.podcast_id,
        title=ep.title,
        published_at=ep.published_at,
        duration_s=ep.duration_s,
        description=ep.description,
        artwork_url=ep.artwork_url or podcast.artwork_url,
        has_feed_transcript=bool(tags),
        status=EpisodeStatus(proc.status),
        podcast=podcast_hit(podcast),
        guid=ep.guid,
        audio_url=ep.audio_url,
        audio_type=ep.audio_type,
        transcript_tags=tags,
        active_job_id=proc.active_job_id,
    )
