"""Deterministic podcast and episode ids.

``podcast_id = sha256(normalize_feed_url(feed_url))``
``episode_id = sha256(normalize_feed_url(feed_url) + episode_guid(...))``

Both are lowercase hex SHA-256 digests (64 chars) of the UTF-8 encoded string, computed
with plain concatenation (no separator). They are pure functions of their inputs, so the
same episode gets the same id on every run, on every machine ("process once, serve many").

Feed URL normalization rules (``normalize_feed_url``)
-----------------------------------------------------
1. Surrounding whitespace is stripped. A URL without a scheme gets ``https://``.
2. The scheme is lowercased. ``http``, ``feed``, ``itpc``, ``pcast`` and ``podcast`` are
   all rewritten to ``https`` (``http`` and ``https`` are treated as the same feed).
3. The host is lowercased and a trailing dot is removed. ``www.`` is NOT stripped
   (it can be a different server).
4. Userinfo (``user:pass@``) is dropped (credentials must never become part of an id).
5. Default ports are removed (``:80`` and ``:443``); any other port is kept.
6. The fragment (``#...``) is removed.
7. The path keeps its case. Trailing slashes are removed (``/feed/`` -> ``/feed``,
   ``https://host/`` -> ``https://host``). An empty path stays empty.
8. The query string is kept verbatim, in its original order (feeds use tokens there).
   A bare ``?`` with nothing after it is removed.
9. Percent-encoding is not re-encoded or decoded.

Episode guid rules (``episode_guid``)
-------------------------------------
1. The item's ``<guid>`` (stripped of surrounding whitespace) when present and non-empty.
   It is case sensitive and used verbatim (not URL-normalized, even when it looks like one).
2. Else the audio enclosure URL, stripped (verbatim, not normalized).
3. Else ``"{title}|{published}"`` where ``title`` is the stripped title (may be empty) and
   ``published`` is the publication time as UTC ISO-8601 ``YYYY-MM-DDTHH:MM:SSZ``, or an
   empty string when the item has no date.

The feed URL that goes into an id is the podcast's canonical feed URL as first recorded
(from iTunes), never the URL a fetch was redirected to, so a feed host's redirect does not
change existing ids.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from urllib.parse import urlsplit, urlunsplit

_HTTPS_ALIASES = {"http", "https", "feed", "itpc", "pcast", "podcast"}
_DEFAULT_PORTS = {80, 443}


def normalize_feed_url(url: str) -> str:
    """Normalize a feed URL for hashing. See the module docstring for the rules."""
    raw = url.strip()
    if not raw:
        raise ValueError("feed URL is empty")
    if "://" not in raw:
        raw = "https://" + raw.lstrip("/")
    parts = urlsplit(raw)
    scheme = parts.scheme.lower()
    if scheme in _HTTPS_ALIASES:
        scheme = "https"
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        raise ValueError("feed URL has no host")
    if ":" in host:  # IPv6 literal
        host = f"[{host}]"
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError("feed URL has an invalid port") from exc
    netloc = host if port is None or port in _DEFAULT_PORTS else f"{host}:{port}"
    path = parts.path.rstrip("/")
    return urlunsplit((scheme, netloc, path, parts.query, ""))


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def podcast_id(feed_url: str) -> str:
    """``sha256(normalize_feed_url(feed_url))`` as 64 lowercase hex chars."""
    return _sha256(normalize_feed_url(feed_url))


def episode_guid(
    guid: str | None,
    enclosure_url: str | None,
    title: str | None,
    published_at: datetime | None,
) -> str:
    """The identity string of an item: guid, else enclosure URL, else ``title|published``."""
    if guid and guid.strip():
        return guid.strip()
    if enclosure_url and enclosure_url.strip():
        return enclosure_url.strip()
    published = ""
    if published_at is not None:
        aware = published_at if published_at.tzinfo else published_at.replace(tzinfo=UTC)
        published = aware.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"{(title or '').strip()}|{published}"


def episode_id(feed_url: str, guid: str) -> str:
    """``sha256(normalize_feed_url(feed_url) + guid)`` as 64 lowercase hex chars.

    ``guid`` must already be the resolved identity string from :func:`episode_guid`.
    """
    return _sha256(normalize_feed_url(feed_url) + guid)
