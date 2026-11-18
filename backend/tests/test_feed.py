"""RSS fetch and parse against fixtures. No network."""

from __future__ import annotations

import httpx
import pytest
import respx

from discovery import feed as feed_mod
from discovery.feed import (
    FeedError,
    fetch_and_parse,
    fetch_feed,
    html_to_text,
    parse_duration,
    parse_feed,
)
from discovery.ids import episode_id
from tests.conftest import FIXTURES

BUZZ = (FIXTURES / "rss_buzzcast_trimmed.xml").read_bytes()
EDGE = (FIXTURES / "rss_edge_cases.xml").read_bytes()
BUZZ_URL = "https://feeds.buzzsprout.com/231452.rss"
EDGE_URL = "https://edge.example/feed"


@pytest.mark.parametrize(
    ("raw", "seconds"),
    [
        ("01:02:03", 3723),
        ("1:02:03", 3723),
        ("45:30", 2730),
        ("3600", 3600),
        (" 3600 ", 3600),
        ("123.7", 123),
        ("1:05", 65),
        ("00:00:59.9", 59),
        (90, 90),
        ("", None),
        (None, None),
        ("unknown", None),
        ("1:2:3:4", None),
        ("-5", None),
        ("1::2", None),
    ],
)
def test_parse_duration(raw: object, seconds: int | None) -> None:
    assert parse_duration(raw) == seconds


def test_html_to_text_strips_and_truncates() -> None:
    assert html_to_text("<p>a &amp; <b>b</b></p><p>c</p>") == "a & b c"
    assert html_to_text("   ") is None
    assert html_to_text("<p>hi</p><script>alert(1)</script><style>p{}</style>") == "hi"
    long = html_to_text("word " * 1000)
    assert long is not None and len(long) <= 2000 and long.endswith("…")


def test_transcript_tags_all_types_kept_in_feed_order() -> None:
    parsed = parse_feed(BUZZ, BUZZ_URL)
    assert parsed.title == "Buzzcast"
    assert len(parsed.episodes) == 3
    first = parsed.episodes[0]
    assert first.guid == "Buzzsprout-20079447"
    assert first.id == episode_id(BUZZ_URL, "Buzzsprout-20079447")
    assert [t.type for t in first.transcript_tags] == [
        "text/html",
        "application/json",
        "application/x-subrip",
        "text/vtt",
    ]
    assert first.transcript_tags[3].url.endswith("/transcript.vtt")
    assert first.duration_s == 3853
    assert first.audio_type == "audio/mpeg"
    assert first.published_at is not None and first.published_at.isoformat().startswith(
        "2026-11-06T13:00:00"
    )
    assert first.description and "<" not in first.description


def test_edge_cases() -> None:
    parsed = parse_feed(EDGE, EDGE_URL)
    by_title = {e.title: e for e in parsed.episodes}
    # video-only, no-enclosure and duplicate-guid items are skipped
    assert parsed.skipped_items == 3
    assert set(by_title) == {
        "Hour-long, HH:MM:SS",
        "No guid, MM:SS",
        "No guid, no type, seconds with spaces",
        "Fractional and garbage durations",
        "Undated episode",
    }
    one = by_title["Hour-long, HH:MM:SS"]
    assert one.duration_s == 3723
    assert one.artwork_url == "https://edge.example/ep1.jpg"
    assert one.description == "Show notes & links One Two"
    assert one.audio_url == "https://cdn.edge.example/ep1.mp3"  # first item wins over its dup
    assert [t.as_json() for t in one.transcript_tags] == [
        {
            "url": "https://edge.example/ep1.srt",
            "type": "application/x-subrip",
            "language": None,
            "rel": None,
        },
        {
            "url": "https://edge.example/ep1.vtt",
            "type": "text/vtt",
            "language": "en",
            "rel": "captions",
        },
    ]
    # missing guid -> enclosure URL is the identity
    no_guid = by_title["No guid, MM:SS"]
    assert no_guid.guid == "https://cdn.edge.example/ep2.m4a?src=rss"
    assert no_guid.id == episode_id(EDGE_URL, "https://cdn.edge.example/ep2.m4a?src=rss")
    assert no_guid.duration_s == 2730
    untyped = by_title["No guid, no type, seconds with spaces"]
    assert untyped.audio_type is None and untyped.duration_s == 3600
    assert by_title["Fractional and garbage durations"].duration_s is None
    assert by_title["Undated episode"].published_at is None
    assert parsed.artwork_url == "https://edge.example/cover.jpg"
    assert parsed.author == "Edge Hosts"


def test_parse_is_deterministic() -> None:
    a = [e.id for e in parse_feed(EDGE, EDGE_URL).episodes]
    b = [e.id for e in parse_feed(EDGE, "http://EDGE.example/feed/").episodes]
    assert a == b


def test_broken_xml_falls_back_to_single_transcript_tag() -> None:
    broken = BUZZ.replace(b"</channel>", b"<unclosed></channel>")
    parsed = parse_feed(broken, BUZZ_URL)
    assert parsed.episodes, "feedparser still recovers the items"
    assert all(len(e.transcript_tags) <= 1 for e in parsed.episodes)


def test_garbage_is_feed_error() -> None:
    with pytest.raises(FeedError):
        parse_feed(b"\x00\x01 not xml at all", EDGE_URL)


@respx.mock
async def test_fetch_follows_redirect_and_keeps_canonical_ids() -> None:
    respx.get(BUZZ_URL).mock(
        return_value=httpx.Response(
            301, headers={"Location": "https://rss.buzzsprout.com/231452.rss"}
        )
    )
    final = respx.get("https://rss.buzzsprout.com/231452.rss").mock(
        return_value=httpx.Response(200, content=BUZZ)
    )
    async with httpx.AsyncClient() as client:
        fetched = await fetch_feed(client, BUZZ_URL)
        parsed = await fetch_and_parse(client, BUZZ_URL)
    assert fetched.final_url == "https://rss.buzzsprout.com/231452.rss"
    assert final.calls.last.request.headers["user-agent"].startswith("PodderSum/")
    # ids come from the canonical (pre-redirect) feed URL
    assert parsed.episodes[0].id == episode_id(BUZZ_URL, "Buzzsprout-20079447")


@respx.mock
async def test_fetch_errors() -> None:
    respx.get("https://a.example/404").mock(return_value=httpx.Response(404))
    respx.get("https://a.example/slow").mock(side_effect=httpx.ReadTimeout("slow"))
    respx.get("https://a.example/loop").mock(
        return_value=httpx.Response(302, headers={"Location": "https://a.example/loop"})
    )
    async with httpx.AsyncClient(max_redirects=3) as client:
        for path in ("404", "slow", "loop"):
            with pytest.raises(FeedError):
                await fetch_feed(client, f"https://a.example/{path}")


@respx.mock
async def test_fetch_size_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(feed_mod, "MAX_FEED_BYTES", 1000)
    respx.get("https://a.example/big").mock(return_value=httpx.Response(200, content=b"x" * 5000))
    async with httpx.AsyncClient() as client:
        with pytest.raises(FeedError, match="size cap"):
            await fetch_feed(client, "https://a.example/big")
