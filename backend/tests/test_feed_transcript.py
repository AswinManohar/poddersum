"""Feed transcript tags: preference, parsers on recorded/edge fixtures, fetch fallback."""

from __future__ import annotations

import httpx
import pytest
import respx

from ingest.feed_transcript import (
    fetch_feed_transcript,
    parse_json_transcript,
    parse_srt,
    parse_timestamp,
    parse_transcript,
    parse_vtt,
    rank_tags,
    tag_format,
)
from tests.conftest import FIXTURES

BASE = "https://www.buzzsprout.com/231452/20079447/transcript"
BUZZ_TAGS = [
    {"url": BASE, "type": "text/html"},
    {"url": BASE + ".json", "type": "application/json"},
    {"url": BASE + ".srt", "type": "application/x-subrip"},
    {"url": BASE + ".vtt", "type": "text/vtt"},
]


def _fx(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


@pytest.mark.parametrize(
    ("tag", "fmt"),
    [
        ({"url": "x", "type": "text/vtt"}, "vtt"),
        ({"url": "x", "type": "text/vtt; charset=utf-8"}, "vtt"),
        ({"url": "x", "type": "application/x-subrip"}, "srt"),
        ({"url": "x", "type": "application/srt"}, "srt"),
        ({"url": "x", "type": "text/srt"}, "srt"),
        ({"url": "x", "type": "application/json"}, "json"),
        ({"url": "x", "type": "text/html"}, None),
        ({"url": "x", "type": "text/plain"}, None),
        ({"url": "https://h/t.vtt?x=1", "type": None}, "vtt"),
        ({"url": "https://h/t.SRT"}, "srt"),
        ({"url": "https://h/t.txt"}, None),
    ],
)
def test_tag_format(tag: dict, fmt: str | None) -> None:
    assert tag_format(tag) == fmt


def test_rank_tags_prefers_vtt_then_srt_then_json_and_drops_html() -> None:
    ranked = rank_tags(BUZZ_TAGS)
    assert [c.format for c in ranked] == ["vtt", "srt", "json"]
    assert ranked[0].url.endswith(".vtt")
    assert rank_tags([{"url": "a", "type": "text/html"}]) == []


def test_parse_timestamp() -> None:
    assert parse_timestamp("00:00:01.500") == 1500
    assert parse_timestamp("01:02.003") == 62_003
    assert parse_timestamp("1:00:00,250") == 3_600_250
    assert parse_timestamp("00:05") == 5000


def test_parse_recorded_buzzcast_vtt_voice_tags() -> None:
    cues = parse_vtt(_fx("transcript_buzzcast.vtt").decode())
    assert len(cues) > 10
    assert cues[0].start_ms == 80 and cues[0].end_ms == 2000
    assert cues[0].speaker == "Jordan"
    assert cues[0].text == "Have you guys seen this new podcast discovery app?"
    assert {"Jordan", "Alban", "Kevin"} <= {c.speaker for c in cues}
    assert all("<" not in c.text for c in cues)


def test_parse_vtt_edge_cases() -> None:
    cues = parse_vtt(_fx("transcript_edge.vtt").decode())
    texts = [c.text for c in cues]
    assert texts[0] == "Welcome to the show, everyone."
    assert cues[0].speaker == "Ana Lopez"  # <v.class Name>
    assert cues[0].start_ms == 1000 and cues[0].end_ms == 4500  # MM:SS.mmm + settings
    assert texts[1] == "we have & a guest today."  # tags, entities, inline timestamps
    assert cues[1].speaker == "Ana Lopez"  # carried forward
    one_hour = next(c for c in cues if c.text == "One hour in.")
    assert one_hour.start_ms == 3_600_000 and one_hour.speaker == "Bob"
    prefixed = next(c for c in cues if c.text == "Labelled with a prefix.")
    assert prefixed.speaker == "Bob"
    assert all("NOTE" not in t and "cue" not in t for t in texts)


def test_parse_srt_prefix_speakers_carry_forward() -> None:
    cues = parse_srt(_fx("transcript_edge.srt").decode())
    assert [c.speaker for c in cues] == ["Host", "Host", "Guest Name", "Guest Name"]
    assert cues[0].text == "Hello and welcome to the show."
    assert cues[1].start_ms == 3000 and cues[1].end_ms == 5500
    assert cues[3].text == "At 10:30 we started."  # digits: not a speaker prefix


def test_parse_recorded_buzzcast_srt() -> None:
    cues = parse_srt(_fx("transcript_buzzcast.srt").decode())
    assert cues[0].speaker == "Jordan"
    assert cues[0].text.startswith("Have you guys seen this new podcast")
    assert {"Jordan", "Alban", "Kevin"} <= {c.speaker for c in cues}
    assert all(c.end_ms >= c.start_ms for c in cues)


def test_parse_recorded_podcasting20_json_word_level() -> None:
    cues = parse_json_transcript(_fx("transcript_buzzcast.json"))
    assert cues[0].text == "Have" and cues[0].start_ms == 80 and cues[0].speaker == "Jordan"
    assert all(c.text.strip() for c in cues)  # whitespace-only bodies dropped
    assert {"Jordan", "Alban"} <= {c.speaker for c in cues}


def test_parse_json_missing_end_uses_next_start() -> None:
    data = {
        "segments": [
            {"startTime": 1.0, "body": "a", "speaker": "X"},
            {"startTime": "2.5", "endTime": 3, "body": "b"},
            {"body": "no start"},
        ]
    }
    cues = parse_json_transcript(data)
    assert [(c.start_ms, c.end_ms, c.speaker) for c in cues] == [
        (1000, 2500, "X"),
        (2500, 3000, "X"),
    ]


@pytest.mark.parametrize(
    ("content", "fmt"),
    [
        (b"not json", "json"),
        (b'{"no": "segments"}', "json"),
        (b"no header\n\n00:01.000 --> 00:02.000\nx", "vtt"),
    ],
)
def test_malformed_raises(content: bytes, fmt: str) -> None:
    with pytest.raises(ValueError):
        parse_transcript(content, fmt)


def test_malformed_vtt_without_cues_yields_nothing() -> None:
    assert parse_transcript(_fx("transcript_malformed.vtt"), "vtt") == []


@respx.mock
async def test_fetch_prefers_vtt_and_records_source() -> None:
    vtt = respx.get(BASE + ".vtt").mock(
        return_value=httpx.Response(200, content=_fx("transcript_buzzcast.vtt"))
    )
    srt = respx.get(BASE + ".srt").mock(return_value=httpx.Response(200, text="unused"))
    async with httpx.AsyncClient() as http:
        result = await fetch_feed_transcript(http, BUZZ_TAGS)
    assert result is not None and vtt.called and not srt.called
    assert result.source == "feed" and result.cost_usd == 0
    assert result.source_url == BASE + ".vtt"
    assert result.raw["format"] == "vtt" and result.raw["cues"][0]["speaker"] == "Jordan"


@respx.mock
async def test_fetch_falls_through_broken_vtt_to_srt() -> None:
    respx.get(BASE + ".vtt").mock(return_value=httpx.Response(404))
    respx.get(BASE + ".srt").mock(
        return_value=httpx.Response(200, content=_fx("transcript_buzzcast.srt"))
    )
    async with httpx.AsyncClient() as http:
        result = await fetch_feed_transcript(http, BUZZ_TAGS)
    assert result is not None and result.raw["format"] == "srt"


@respx.mock
async def test_fetch_follows_redirects() -> None:
    respx.get(BASE + ".vtt").mock(
        return_value=httpx.Response(302, headers={"Location": "https://cdn.example/t.vtt"})
    )
    respx.get("https://cdn.example/t.vtt").mock(
        return_value=httpx.Response(200, content=_fx("transcript_edge.vtt"))
    )
    async with httpx.AsyncClient() as http:
        result = await fetch_feed_transcript(http, [BUZZ_TAGS[3]])
    assert result is not None and result.cues[0].speaker == "Ana Lopez"


@respx.mock
async def test_fetch_all_unusable_returns_none() -> None:
    respx.get(BASE + ".vtt").mock(
        return_value=httpx.Response(200, content=_fx("transcript_malformed.vtt"))
    )
    respx.get(BASE + ".srt").mock(side_effect=httpx.ConnectError("down"))
    respx.get(BASE + ".json").mock(return_value=httpx.Response(200, text="{oops"))
    html = respx.get(BASE).mock(
        return_value=httpx.Response(200, content=_fx("transcript_plain.html"))
    )
    async with httpx.AsyncClient() as http:
        assert await fetch_feed_transcript(http, BUZZ_TAGS) is None
    assert not html.called  # html is never even fetched


@respx.mock
async def test_fetch_size_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    import ingest.feed_transcript as ft

    monkeypatch.setattr(ft, "MAX_TRANSCRIPT_BYTES", 100)
    respx.get(BASE + ".vtt").mock(
        return_value=httpx.Response(200, content=_fx("transcript_buzzcast.vtt"))
    )
    async with httpx.AsyncClient() as http:
        with pytest.raises(ValueError):
            await ft.fetch_bytes(http, BASE + ".vtt", max_bytes=100)
