"""Pure helpers: link parsing, podcast/episode matching, markdown formatting."""

from __future__ import annotations

from conftest import fixture_json

from poddersum_mcp import format as fmt
from poddersum_mcp.resolve import parse_link, pick_episode, pick_podcast
from poddersum_mcp.service import build_question

EPISODES = fixture_json("episodes.json")["episodes"]
RESULTS = fixture_json("search.json")["results"]


def test_timestamps_and_links() -> None:
    assert fmt.timestamp(0) == "0:00" and fmt.timestamp(65_999) == "1:05"
    assert fmt.timestamp(3_725_000) == "1:02:05"
    assert fmt.audio_link("https://a.example/x.mp3?k=1#old", 65_400) == "https://a.example/x.mp3?k=1#t=65"
    assert fmt.duration(3725) == "1 h 2 min" and fmt.duration(42) == "1 min" and fmt.duration(None) is None


def test_claims_dedupe_and_sections() -> None:
    claims = [
        {"text": "A", "citations": [{"segment_id": 1, "start_ms": 1000}, {"segment_id": 2, "start_ms": 1500}],
         "section": "who_its_for"},
        {"text": "B", "citations": [{"segment_id": 3, "start_ms": 61000}], "section": "best_10_minutes"},
    ]  # fmt: skip
    md = fmt.claims_markdown(claims, "https://a/x.mp3")
    assert md.count("[0:01]") == 1  # same second cited twice -> one link
    assert "**Who it's for**" in md and "**Best 10 minutes**" in md and "[1:01](https://a/x.mp3#t=61)" in md
    assert "No grounded claims" in fmt.claims_markdown([], None)


def test_parse_links() -> None:
    apple = parse_link("see https://podcasts.apple.com/us/podcast/hidden-brain/id1028908750?i=1000650000000.")
    assert apple.kind == "apple" and apple.show_query == "hidden brain"
    assert apple.itunes_id == 1028908750 and apple.apple_episode_id == "1000650000000"
    assert parse_link("https://open.spotify.com/show/abc").kind == "spotify"
    assert parse_link("https://feeds.example.com/show/rss").kind == "rss"
    assert parse_link("Hidden Brain") is None


def test_pick_podcast() -> None:
    assert pick_podcast(RESULTS, "hidden brain kids")["title"] == "Hidden Brain Kids"
    assert pick_podcast(RESULTS, "whatever", itunes_id=1500000001)["title"] == "Hidden Brain Kids"
    assert pick_podcast(RESULTS, "hiden brain")["title"] == "Hidden Brain"  # iTunes' top hit
    assert pick_podcast([], "x") is None


def test_pick_episode() -> None:
    assert pick_episode(EPISODES, None).episode["title"].startswith("The Science")
    assert pick_episode(EPISODES, "the latest episode").episode is EPISODES[0]
    assert pick_episode(EPISODES, "#212").episode["title"].startswith("#212")
    assert pick_episode(EPISODES, "212").episode["title"].startswith("#212")
    assert pick_episode(EPISODES, "second chances").episode is EPISODES[0]
    assert pick_episode(EPISODES, "the one about sleep").episode is EPISODES[1]
    assert pick_episode(EPISODES, "Rethinking Rest: Part 2").episode is EPISODES[4]
    amb = pick_episode(EPISODES, "rest")
    assert amb.episode is None and len(amb.candidates) == 5
    assert pick_episode(EPISODES, "quantum chromodynamics").episode is None
    assert pick_episode([], "x").episode is None


def test_build_question() -> None:
    assert "TL;DR" in build_question("tldr", None)
    q = build_question("verdict", " for a founder ")
    assert "worth listening" in q and q.endswith("for a founder")
