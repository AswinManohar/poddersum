"""Pick a podcast and an episode from loose user input (names, hints, links).

Pure functions over API dicts so they are easy to test and shared by the MCP tools.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

JSON = dict[str, Any]

LATEST_WORDS = re.compile(
    r"^(the\s+)?(latest|newest|most\s+recent|last|recent|new|current)(\s+(one|episode|ep))?$"
)
EPISODE_NUMBER = re.compile(r"(?:#|\bep(?:isode)?\.?\s*|\bno\.?\s*)(\d{1,5})\b", re.I)
STOPWORDS = {
    "the",
    "a",
    "an",
    "of",
    "and",
    "or",
    "to",
    "in",
    "on",
    "for",
    "with",
    "about",
    "episode",
    "ep",
    "podcast",
    "show",
    "is",
    "it",
    "this",
    "that",
    "from",
    "at",
    "by",
    "my",
    "me",
    "one",
    "where",
    "when",
    "they",
    "he",
    "she",
    "what",
    "which",
    "was",
    "were",
    "talk",
    "talks",
    "talked",
    "discuss",
    "discussed",
    "said",
    "says",
    "new",
    "recent",
}
SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
MATCH_THRESHOLD = 0.55
MATCH_MARGIN = 0.08


@dataclass
class LinkInfo:
    """What we could read from a pasted link."""

    kind: str  # "apple" | "spotify" | "rss" | "other"
    show_query: str | None = None
    itunes_id: int | None = None
    apple_episode_id: str | None = None
    url: str = ""


@dataclass
class EpisodeMatch:
    episode: JSON | None
    candidates: list[JSON] = field(default_factory=list)
    reason: str = ""


def is_episode_id(value: str | None) -> bool:
    return bool(value) and bool(SHA256_HEX.match(value.strip()))


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _tokens(text: str) -> set[str]:
    return {t for t in _norm(text).split() if t not in STOPWORDS and (len(t) > 1 or t.isdigit())}


def parse_link(text: str | None) -> LinkInfo | None:
    """Recognise Apple Podcasts, Spotify and RSS links; ``None`` for plain text."""
    if not text:
        return None
    m = re.search(r"https?://\S+", text)
    if not m:
        return None
    url = m.group(0).rstrip(").,>")
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host.endswith("podcasts.apple.com") or host.endswith("itunes.apple.com"):
        parts = [p for p in parsed.path.split("/") if p]
        slug = next(
            (parts[i + 1] for i, p in enumerate(parts) if p == "podcast" and i + 1 < len(parts)),
            None,
        )
        itunes = next((int(p[2:]) for p in parts if re.fullmatch(r"id\d+", p)), None)
        if slug and slug.startswith("id"):
            slug = None
        episode = parse_qs(parsed.query).get("i", [None])[0]
        show = unquote(slug).replace("-", " ") if slug else None
        return LinkInfo("apple", show, itunes, episode, url)
    if host.endswith("spotify.com") or host.endswith("spotify.link"):
        return LinkInfo("spotify", url=url)
    path = parsed.path.lower()
    if path.endswith((".xml", ".rss")) or "feed" in path or "rss" in path or "feed" in host or "rss" in host:
        return LinkInfo("rss", url=url)
    return LinkInfo("other", url=url)


def pick_podcast(results: list[JSON], query: str, itunes_id: int | None = None) -> JSON | None:
    """Exact iTunes id, then exact normalized title, then iTunes' own top hit."""
    if not results:
        return None
    if itunes_id is not None:
        for hit in results:
            if hit.get("itunes_id") == itunes_id:
                return hit
    q = _norm(query)
    for hit in results:
        if _norm(hit.get("title", "")) == q:
            return hit
    return results[0]


def _score(hint: str, episode: JSON) -> float:
    title = episode.get("title") or ""
    ratio = SequenceMatcher(None, _norm(hint), _norm(title)).ratio()
    hint_tokens = _tokens(hint)
    if not hint_tokens:
        return ratio
    title_tokens = _tokens(title)
    desc_tokens = _tokens(episode.get("description") or "")
    in_title = len(hint_tokens & title_tokens) / len(hint_tokens)
    in_desc = len(hint_tokens & desc_tokens) / len(hint_tokens)
    return max(ratio, in_title, 0.85 * in_desc)


def pick_episode(episodes: list[JSON], hint: str | None) -> EpisodeMatch:
    """``episodes`` are newest first (as the API returns them)."""
    if not episodes:
        return EpisodeMatch(None, [], "The podcast has no episodes with audio.")
    text = (hint or "").strip()
    if not text or LATEST_WORDS.match(_norm(text)):
        return EpisodeMatch(episodes[0], [], "newest episode")
    if text.isdigit() or EPISODE_NUMBER.search(text):
        num = text if text.isdigit() else EPISODE_NUMBER.search(text).group(1)  # type: ignore[union-attr]
        pattern = re.compile(rf"(?:#|\bep(?:isode)?\.?\s*|^|\b){num}\b", re.I)
        numbered = [e for e in episodes if pattern.search(e.get("title") or "")]
        if len(numbered) == 1:
            return EpisodeMatch(numbered[0], [], f"episode number {num}")
        if numbered:
            return EpisodeMatch(None, numbered[:5], f"several episodes match number {num}")
    exact = [e for e in episodes if _norm(e.get("title") or "") == _norm(text)]
    if exact:
        return EpisodeMatch(exact[0], [], "exact title")
    scored = sorted(((_score(text, e), i, e) for i, e in enumerate(episodes)), key=lambda t: (-t[0], t[1]))
    best_score, _, best = scored[0]
    runner_up = scored[1][0] if len(scored) > 1 else 0.0
    if best_score >= MATCH_THRESHOLD and best_score - runner_up >= MATCH_MARGIN:
        return EpisodeMatch(best, [], f"title match ({best_score:.2f})")
    return EpisodeMatch(
        None,
        [e for _, _, e in scored[:5]],
        f'no episode clearly matches "{text}"',
    )
