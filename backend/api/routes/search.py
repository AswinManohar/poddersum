"""``GET /search``: podcast search by name (iTunes Search API)."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Query

from api.deps import ApiError, HttpClientDep, SessionDep, errors
from api.schemas import PodcastHit, SearchResponse
from db.queries import upsert_podcasts
from discovery.itunes import ItunesError, search_podcasts

logger = logging.getLogger(__name__)
router = APIRouter(tags=["discovery"])


@router.get(
    "/search",
    response_model=SearchResponse,
    responses=errors(502),
    summary="Search podcasts by name",
)
async def search(
    session: SessionDep,
    http: HttpClientDep,
    q: Annotated[str, Query(min_length=1, max_length=200, description="Show name or keywords.")],
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> SearchResponse:
    """Shows matching ``q`` that have a public RSS feed. Each hit is stored so that
    ``/podcasts/{id}/episodes`` can resolve its feed."""
    term = q.strip()
    if not term:
        return SearchResponse(query=q, results=[])
    try:
        hits = await search_podcasts(http, term, limit=limit)
    except ItunesError as exc:
        logger.warning("itunes search failed: %s", exc)
        raise ApiError(502, "upstream_error", "Podcast search is unavailable, try again.") from exc
    await upsert_podcasts(session, hits)
    await session.commit()
    return SearchResponse(
        query=term,
        results=[
            PodcastHit(
                id=h.id,
                title=h.title,
                author=h.author,
                artwork_url=h.artwork_url,
                feed_url=h.feed_url,
                genre=h.genre,
                itunes_id=h.itunes_id,
            )
            for h in hits
        ],
    )
