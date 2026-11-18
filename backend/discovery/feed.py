"""RSS feed fetch and parse: episodes, audio enclosures, durations, transcript tags.

``feedparser`` does the general parsing. It keeps only the LAST ``<podcast:transcript>``
of an item, so transcript tags are collected with a second, namespace-aware ElementTree
pass over the same bytes (falling back to feedparser's single tag when the XML is too
broken for ElementTree).
"""

from __future__ import annotations

import asyncio
import calendar
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any

import feedparser
import httpx

from discovery.ids import episode_guid, episode_id

USER_AGENT = "PodderSum/0.1"
FEED_TIMEOUT = httpx.Timeout(15.0, connect=5.0)
MAX_FEED_BYTES = 30 * 1024 * 1024
MAX_REDIRECTS = 5
DESCRIPTION_MAX_CHARS = 2000

_AUDIO_EXTENSIONS = (".mp3", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".wav", ".flac", ".mp4a")
_TRANSCRIPT_LOCAL = "transcript"


class FeedError(RuntimeError):
    """The feed could not be fetched or parsed."""


@dataclass(frozen=True, slots=True)
class TranscriptTagData:
    url: str
    type: str | None
    language: str | None = None
    rel: str | None = None

    def as_json(self) -> dict[str, str | None]:
        return {"url": self.url, "type": self.type, "language": self.language, "rel": self.rel}


@dataclass(frozen=True, slots=True)
class FeedEpisode:
    id: str
    guid: str
    title: str
    description: str | None
    published_at: datetime | None
    duration_s: int | None
    audio_url: str
    audio_type: str | None
    artwork_url: str | None
    transcript_tags: list[TranscriptTagData] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ParsedFeed:
    title: str | None
    author: str | None
    artwork_url: str | None
    episodes: list[FeedEpisode]
    skipped_items: int = 0


@dataclass(frozen=True, slots=True)
class FetchedFeed:
    content: bytes
    final_url: str


# --- fetch ------------------------------------------------------------------


async def fetch_feed(client: httpx.AsyncClient, url: str) -> FetchedFeed:
    """GET the feed, following redirects, enforcing a timeout and a size cap."""
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/rss+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.5",
    }
    try:
        async with client.stream(
            "GET",
            url,
            headers=headers,
            timeout=FEED_TIMEOUT,
            follow_redirects=True,
        ) as resp:
            if resp.status_code != 200:
                raise FeedError(f"feed returned HTTP {resp.status_code}")
            declared = resp.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > MAX_FEED_BYTES:
                raise FeedError("feed is larger than the size cap")
            chunks: list[bytes] = []
            total = 0
            async for chunk in resp.aiter_bytes():
                total += len(chunk)
                if total > MAX_FEED_BYTES:
                    raise FeedError("feed is larger than the size cap")
                chunks.append(chunk)
            return FetchedFeed(content=b"".join(chunks), final_url=str(resp.url))
    except httpx.TooManyRedirects as exc:
        raise FeedError("feed redirected too many times") from exc
    except httpx.TimeoutException as exc:
        raise FeedError("feed fetch timed out") from exc
    except httpx.HTTPError as exc:
        raise FeedError(f"feed fetch failed ({type(exc).__name__})") from exc


# --- field helpers ------------------------------------------------------------


def parse_duration(value: Any) -> int | None:
    """``itunes:duration`` to whole seconds. Accepts ``HH:MM:SS``, ``MM:SS``, ``SS``,
    fractional seconds (``123.5``) and stray whitespace. Returns None when unparseable."""
    if value is None:
        return None
    if isinstance(value, int | float):
        return int(value) if value >= 0 else None
    text = str(value).strip()
    if not text:
        return None
    parts = text.split(":")
    if len(parts) > 3:
        return None
    try:
        nums = [float(p.strip()) for p in parts]
    except ValueError:
        return None
    if any(n < 0 for n in nums):
        return None
    seconds = 0.0
    for n in nums:
        seconds = seconds * 60 + n
    return int(seconds)


class _TextExtractor(HTMLParser):
    _SKIP = {"script", "style", "template"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skipping = 0

    def handle_data(self, data: str) -> None:
        if not self._skipping:
            self.parts.append(data)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP:
            self._skipping += 1
        elif tag in {"p", "br", "li", "div"}:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self._skipping:
            self._skipping -= 1


_WS = re.compile(r"\s+")


def html_to_text(value: str | None, max_chars: int = DESCRIPTION_MAX_CHARS) -> str | None:
    """Strip tags, unescape entities, collapse whitespace and truncate on a word boundary."""
    if not value:
        return None
    parser = _TextExtractor()
    parser.feed(value)
    parser.close()
    text = _WS.sub(" ", "".join(parser.parts)).strip()
    if not text:
        return None
    if len(text) > max_chars:
        cut = text[: max_chars - 1]
        space = cut.rfind(" ")
        if space > max_chars * 0.8:
            cut = cut[:space]
        text = cut.rstrip() + "…"
    return text


def _to_datetime(struct: time.struct_time | None) -> datetime | None:
    if not struct:
        return None
    try:
        return datetime.fromtimestamp(calendar.timegm(struct), tz=UTC)
    except (OverflowError, ValueError, OSError):
        return None


def _audio_enclosure(entry: Any) -> tuple[str, str | None] | None:
    candidates: list[tuple[str, str | None]] = []
    for enc in entry.get("enclosures") or []:
        href = (enc.get("href") or enc.get("url") or "").strip()
        if href:
            candidates.append((href, (enc.get("type") or "").strip().lower() or None))
    for href, mime in candidates:
        if mime and mime.startswith("audio/"):
            return href, mime
    for href, mime in candidates:
        if mime is None or mime == "application/octet-stream":
            path = href.split("?", 1)[0].lower()
            if path.endswith(_AUDIO_EXTENSIONS):
                return href, mime
    return None


def _image_href(obj: Any) -> str | None:
    image = obj.get("image") if hasattr(obj, "get") else None
    if isinstance(image, dict):
        href = image.get("href") or image.get("url")
        if isinstance(href, str) and href.strip():
            return href.strip()
    return None


def _local(tag: Any) -> tuple[str, str]:
    if not isinstance(tag, str):
        return "", ""
    if tag.startswith("{"):
        ns, _, local = tag[1:].partition("}")
        return ns, local
    return "", tag


def _is_podcast_ns(ns: str) -> bool:
    # Official https://podcastindex.org/namespace/1.0 plus the older GitHub URL some hosts use.
    return "podcastindex" in ns.lower() or "podcast-namespace" in ns.lower()


def _transcripts_by_item(content: bytes) -> list[list[TranscriptTagData]] | None:
    """All ``<podcast:transcript>`` tags per item, in document order. None if the XML does not
    parse (the caller then uses feedparser's single tag per item)."""
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return None
    result: list[list[TranscriptTagData]] = []
    for el in root.iter():
        ns, local = _local(el.tag)
        if local not in {"item", "entry"} or (local == "item" and ns):
            continue
        tags: list[TranscriptTagData] = []
        for child in el:
            cns, clocal = _local(child.tag)
            if clocal == _TRANSCRIPT_LOCAL and _is_podcast_ns(cns):
                url = (child.get("url") or "").strip()
                if url:
                    tags.append(
                        TranscriptTagData(
                            url=url,
                            type=(child.get("type") or "").strip() or None,
                            language=(child.get("language") or "").strip() or None,
                            rel=(child.get("rel") or "").strip() or None,
                        )
                    )
        result.append(tags)
    return result


def _feedparser_transcript(entry: Any) -> list[TranscriptTagData]:
    tag = entry.get("podcast_transcript")
    if isinstance(tag, dict) and (tag.get("url") or "").strip():
        return [TranscriptTagData(url=tag["url"].strip(), type=tag.get("type") or None)]
    return []


# --- parse ------------------------------------------------------------------


def parse_feed(content: bytes, feed_url: str) -> ParsedFeed:
    """Parse feed bytes (sync, CPU bound: call via ``asyncio.to_thread``).

    ``feed_url`` is the podcast's canonical feed URL; it is used for episode ids.
    Items without an audio enclosure are skipped. Duplicate ids keep the first item.
    """
    # No HTML sanitizing or URI resolution: descriptions are reduced to plain text below and
    # never rendered as HTML, and skipping both makes large feeds about 4x faster.
    parsed = feedparser.parse(content, sanitize_html=False, resolve_relative_uris=False)
    if parsed.get("bozo") and not parsed.entries and not parsed.feed:
        raise FeedError("feed is not valid RSS/Atom")
    channel = parsed.feed
    channel_author = channel.get("author") or channel.get("itunes_author") or None

    entries = parsed.entries
    transcripts = _transcripts_by_item(content)
    if transcripts is not None and len(transcripts) != len(entries):
        transcripts = None

    episodes: list[FeedEpisode] = []
    seen: set[str] = set()
    skipped = 0
    for index, entry in enumerate(entries):
        enclosure = _audio_enclosure(entry)
        if enclosure is None:
            skipped += 1
            continue
        audio_url, audio_type = enclosure
        title = (entry.get("title") or "").strip()
        published_at = _to_datetime(entry.get("published_parsed") or entry.get("updated_parsed"))
        guid = episode_guid(entry.get("id"), audio_url, title, published_at)
        eid = episode_id(feed_url, guid)
        if eid in seen:
            skipped += 1
            continue
        seen.add(eid)
        description_html = entry.get("summary")
        if not description_html and entry.get("content"):
            description_html = entry["content"][0].get("value")
        episodes.append(
            FeedEpisode(
                id=eid,
                guid=guid,
                title=title or "Untitled episode",
                description=html_to_text(description_html),
                published_at=published_at,
                duration_s=parse_duration(entry.get("itunes_duration")),
                audio_url=audio_url,
                audio_type=audio_type,
                artwork_url=_image_href(entry),
                transcript_tags=(
                    transcripts[index] if transcripts is not None else _feedparser_transcript(entry)
                ),
            )
        )
    return ParsedFeed(
        title=(channel.get("title") or "").strip() or None,
        author=channel_author,
        artwork_url=_image_href(channel),
        episodes=episodes,
        skipped_items=skipped,
    )


async def fetch_and_parse(client: httpx.AsyncClient, feed_url: str) -> ParsedFeed:
    """Fetch ``feed_url`` and parse it off the event loop."""
    fetched = await fetch_feed(client, feed_url)
    return await asyncio.to_thread(parse_feed, fetched.content, feed_url)
