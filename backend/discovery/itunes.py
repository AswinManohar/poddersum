"""iTunes Search API client (podcast names -> feed URLs). Free, no API key.

Docs: https://performance-partners.apple.com/search-api
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from discovery.ids import podcast_id

ITUNES_SEARCH_URL = "https://itunes.apple.com/search"
ITUNES_TIMEOUT = httpx.Timeout(6.0, connect=3.0)
USER_AGENT = "PodderSum/0.1"


class ItunesError(RuntimeError):
    """The iTunes Search API failed (network error, timeout, bad status or bad body)."""


@dataclass(frozen=True, slots=True)
class ItunesPodcast:
    id: str
    feed_url: str
    itunes_id: int | None
    title: str
    author: str | None
    artwork_url: str | None
    genre: str | None


def _artwork(result: dict) -> str | None:
    for key in ("artworkUrl600", "artworkUrl100", "artworkUrl60", "artworkUrl30"):
        value = result.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def parse_results(payload: dict) -> list[ItunesPodcast]:
    """Map an iTunes response body to podcasts. Hits without a ``feedUrl`` are dropped,
    as are duplicates of the same (normalized) feed."""
    hits: list[ItunesPodcast] = []
    seen: set[str] = set()
    for result in payload.get("results") or []:
        if not isinstance(result, dict):
            continue
        feed_url = result.get("feedUrl")
        if not isinstance(feed_url, str) or not feed_url.strip():
            continue
        try:
            pid = podcast_id(feed_url)
        except ValueError:
            continue
        if pid in seen:
            continue
        seen.add(pid)
        collection_id = result.get("collectionId")
        hits.append(
            ItunesPodcast(
                id=pid,
                feed_url=feed_url.strip(),
                itunes_id=collection_id if isinstance(collection_id, int) else None,
                title=str(result.get("collectionName") or result.get("trackName") or "").strip()
                or "Untitled podcast",
                author=(result.get("artistName") or None),
                artwork_url=_artwork(result),
                genre=(result.get("primaryGenreName") or None),
            )
        )
    return hits


async def search_podcasts(
    client: httpx.AsyncClient, term: str, *, limit: int = 20
) -> list[ItunesPodcast]:
    """Search podcasts by name. Raises :class:`ItunesError` on any upstream failure."""
    params = {
        "media": "podcast",
        "entity": "podcast",
        "term": term,
        "limit": str(limit),
    }
    try:
        resp = await client.get(
            ITUNES_SEARCH_URL,
            params=params,
            timeout=ITUNES_TIMEOUT,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
    except httpx.TimeoutException as exc:
        raise ItunesError("iTunes Search timed out") from exc
    except httpx.HTTPError as exc:
        raise ItunesError(f"iTunes Search request failed ({type(exc).__name__})") from exc
    if resp.status_code != 200:
        raise ItunesError(f"iTunes Search returned HTTP {resp.status_code}")
    try:
        payload = resp.json()
    except ValueError as exc:
        raise ItunesError("iTunes Search returned an invalid body") from exc
    if not isinstance(payload, dict):
        raise ItunesError("iTunes Search returned an invalid body")
    return parse_results(payload)
