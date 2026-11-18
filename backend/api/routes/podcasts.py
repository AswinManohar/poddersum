"""``GET /podcasts/{podcast_id}/episodes``: episodes from the podcast's RSS feed."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Path, Query

from api.deps import ApiError, HttpClientDep, SessionDep, errors
from api.schemas import EpisodeRef, EpisodeStatus, PodcastEpisodes, PodcastHit
from db.models import Podcast
from db.queries import episode_processing, fill_podcast_from_feed, get_podcast, upsert_episodes
from discovery.feed import FeedError, fetch_and_parse

logger = logging.getLogger(__name__)
router = APIRouter(tags=["discovery"])

PodcastIdPath = Annotated[str, Path(pattern=r"^[0-9a-f]{64}$", description="Podcast id.")]
_OLDEST = datetime.min.replace(tzinfo=UTC)


def podcast_hit(p: Podcast) -> PodcastHit:
    return PodcastHit(
        id=p.id,
        title=p.title,
        author=p.author,
        artwork_url=p.artwork_url,
        feed_url=p.feed_url,
        genre=p.genre,
        itunes_id=p.itunes_id,
    )


@router.get(
    "/podcasts/{podcast_id}/episodes",
    response_model=PodcastEpisodes,
    responses=errors(404, 502),
    summary="List a podcast's episodes (newest first)",
)
async def list_episodes(
    podcast_id: PodcastIdPath,
    session: SessionDep,
    http: HttpClientDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> PodcastEpisodes:
    """Fetches the RSS feed live, stores every episode that has audio, and returns the
    newest ``limit``. The podcast must have been returned by ``/search`` first."""
    podcast = await get_podcast(session, podcast_id)
    if podcast is None:
        raise ApiError(404, "not_found", "Unknown podcast. Search for it first.")
    try:
        feed = await fetch_and_parse(http, podcast.feed_url)
    except FeedError as exc:
        logger.warning("feed fetch failed for %s: %s", podcast_id, exc)
        raise ApiError(502, "upstream_error", "The podcast's feed could not be read.") from exc

    await fill_podcast_from_feed(session, podcast, feed)
    await upsert_episodes(session, podcast.id, feed.episodes)
    await session.commit()

    ordered = sorted(feed.episodes, key=lambda e: e.published_at or _OLDEST, reverse=True)
    page = ordered[:limit]
    statuses = await episode_processing(session, [e.id for e in page])  # one query
    return PodcastEpisodes(
        podcast=podcast_hit(podcast),
        total=len(ordered),
        episodes=[
            EpisodeRef(
                id=e.id,
                podcast_id=podcast.id,
                title=e.title,
                published_at=e.published_at,
                duration_s=e.duration_s,
                description=e.description,
                artwork_url=e.artwork_url or podcast.artwork_url,
                has_feed_transcript=bool(e.transcript_tags),
                status=EpisodeStatus(statuses[e.id].status)
                if e.id in statuses
                else EpisodeStatus.new,
            )
            for e in page
        ],
    )
