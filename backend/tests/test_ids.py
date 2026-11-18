"""Deterministic ids and feed URL normalization (discovery/ids.py)."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta, timezone

import pytest

from discovery.ids import episode_guid, episode_id, normalize_feed_url, podcast_id

CANON = "https://feeds.example.com/show/rss"


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("https://feeds.example.com/show/rss", CANON),
        ("  https://feeds.example.com/show/rss \n", CANON),
        ("http://feeds.example.com/show/rss", CANON),
        ("HTTPS://FEEDS.Example.COM/show/rss", CANON),
        ("https://feeds.example.com:443/show/rss", CANON),
        ("http://feeds.example.com:80/show/rss", CANON),
        ("https://feeds.example.com/show/rss/", CANON),
        ("https://feeds.example.com/show/rss//", CANON),
        ("https://feeds.example.com/show/rss#latest", CANON),
        ("https://feeds.example.com/show/rss?", CANON),
        ("https://feeds.example.com./show/rss", CANON),
        ("https://user:pw@feeds.example.com/show/rss", CANON),
        ("feed://feeds.example.com/show/rss", CANON),
        ("itpc://feeds.example.com/show/rss", CANON),
        ("pcast://feeds.example.com/show/rss", CANON),
        ("feeds.example.com/show/rss", CANON),
        # kept as-is
        ("https://feeds.example.com/Show/RSS", "https://feeds.example.com/Show/RSS"),
        ("https://feeds.example.com:8443/rss", "https://feeds.example.com:8443/rss"),
        ("https://feeds.example.com/rss?b=2&a=1", "https://feeds.example.com/rss?b=2&a=1"),
        ("https://www.example.com/rss", "https://www.example.com/rss"),
        ("https://example.com/", "https://example.com"),
        ("https://example.com/a%20b", "https://example.com/a%20b"),
    ],
)
def test_normalize_feed_url(given: str, expected: str) -> None:
    assert normalize_feed_url(given) == expected


@pytest.mark.parametrize("bad", ["", "   ", "https://", "https://host:notaport/x"])
def test_normalize_rejects_garbage(bad: str) -> None:
    with pytest.raises(ValueError):
        normalize_feed_url(bad)


def test_podcast_id_is_sha256_of_normalized_url() -> None:
    expected = hashlib.sha256(CANON.encode()).hexdigest()
    assert podcast_id("http://FEEDS.example.com/show/rss/") == expected
    assert len(expected) == 64


def test_episode_id_is_sha256_of_url_plus_guid() -> None:
    expected = hashlib.sha256((CANON + "guid-1").encode()).hexdigest()
    assert episode_id("http://feeds.example.com:80/show/rss#x", "guid-1") == expected


def test_ids_are_stable_known_values() -> None:
    # Pinned values (cross-checked with `printf %s ... | sha256sum`). If these change,
    # every stored id in every database changes.
    assert podcast_id("http://feeds.simplecast.com/6HKOhNgS/") == (
        "411674d7e8654610c29ab21ad00777df2d2639f7d4427d18c6d1c0feee6700d5"
    )
    assert episode_id("https://feeds.buzzsprout.com/231452.rss", "Buzzsprout-20079447") == (
        "bcaf69a2cc1376a88babc913fcc0ec70a00aa76a4c4723a0a81ec0af22aaa1c0"
    )


def test_guid_differs_means_id_differs() -> None:
    assert episode_id(CANON, "a") != episode_id(CANON, "b")
    assert episode_id(CANON, "a") != episode_id(CANON + "2", "a")


def test_guid_is_case_sensitive_and_stripped() -> None:
    assert episode_guid("  Abc-1 ", None, None, None) == "Abc-1"
    assert episode_guid("abc-1", None, None, None) != episode_guid("ABC-1", None, None, None)


def test_guid_fallback_enclosure_then_title_date() -> None:
    when = datetime(2026, 11, 2, 10, 0, tzinfo=UTC)
    assert episode_guid(None, " https://cdn/x.mp3 ", "T", when) == "https://cdn/x.mp3"
    assert episode_guid("", None, " Title ", when) == "Title|2026-11-02T10:00:00Z"
    assert episode_guid("  ", "", "Title", None) == "Title|"
    plus_two = datetime(2026, 11, 2, 12, 0, tzinfo=timezone(timedelta(hours=2)))
    assert episode_guid(None, None, "T", plus_two) == "T|2026-11-02T10:00:00Z"
    naive = datetime(2026, 11, 2, 10, 0)
    assert episode_guid(None, None, "T", naive) == "T|2026-11-02T10:00:00Z"
