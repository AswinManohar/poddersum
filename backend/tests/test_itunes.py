"""iTunes Search client against a recorded (trimmed) response. No network."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from discovery.ids import podcast_id
from discovery.itunes import ITUNES_SEARCH_URL, ItunesError, parse_results, search_podcasts
from tests.conftest import FIXTURES

RECORDED = json.loads((FIXTURES / "itunes_search_hard_fork.json").read_text())


def test_parse_results_maps_hits_and_drops_missing_feed() -> None:
    hits = parse_results(RECORDED)
    # 4 recorded results; the last one has no feedUrl and is dropped.
    assert len(RECORDED["results"]) == 4
    assert [h.title for h in hits] == ["Hard Fork", "Hard Fork Decentralized", "Hard Fork Live"]
    first = hits[0]
    assert first.feed_url == "https://feeds.simplecast.com/6HKOhNgS"
    assert first.id == podcast_id("https://feeds.simplecast.com/6HKOhNgS")
    assert first.itunes_id == 1528594034
    assert first.author == "The New York Times"
    assert first.genre == "Technology"
    assert first.artwork_url and first.artwork_url.endswith("600x600bb.jpg")


def test_parse_results_dedupes_same_feed() -> None:
    a = dict(RECORDED["results"][0])
    b = dict(a, feedUrl="http://feeds.simplecast.com/6HKOhNgS/", collectionName="Dup")
    assert [h.title for h in parse_results({"results": [a, b]})] == ["Hard Fork"]


def test_parse_results_tolerates_junk() -> None:
    assert parse_results({}) == []
    assert parse_results({"results": [None, 3, {"feedUrl": ""}, {"feedUrl": "https://"}]}) == []


@respx.mock
async def test_search_sends_expected_query() -> None:
    route = respx.get(ITUNES_SEARCH_URL).mock(return_value=httpx.Response(200, json=RECORDED))
    async with httpx.AsyncClient() as client:
        hits = await search_podcasts(client, "hard fork", limit=5)
    assert len(hits) == 3
    params = route.calls.last.request.url.params
    assert params["media"] == "podcast"
    assert params["entity"] == "podcast"
    assert params["term"] == "hard fork"
    assert params["limit"] == "5"
    assert route.calls.last.request.headers["user-agent"].startswith("PodderSum/")


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(503),
        httpx.Response(200, content=b"<html>oops</html>"),
        httpx.Response(200, json=[1, 2]),
    ],
)
@respx.mock
async def test_search_bad_upstream_raises(response: httpx.Response) -> None:
    respx.get(ITUNES_SEARCH_URL).mock(return_value=response)
    async with httpx.AsyncClient() as client:
        with pytest.raises(ItunesError):
            await search_podcasts(client, "x")


@pytest.mark.parametrize("exc", [httpx.ReadTimeout("slow"), httpx.ConnectError("down")])
@respx.mock
async def test_search_network_errors_raise(exc: Exception) -> None:
    respx.get(ITUNES_SEARCH_URL).mock(side_effect=exc)
    async with httpx.AsyncClient() as client:
        with pytest.raises(ItunesError):
            await search_podcasts(client, "x")
