"""Route contract tests: real app, docker Postgres (throwaway schema), respx for upstreams."""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.schemas import EpisodeDetail, ErrorResponse, PodcastEpisodes, SearchResponse
from db.models import Episode, Podcast
from discovery.ids import episode_id, podcast_id
from discovery.itunes import ITUNES_SEARCH_URL
from tests.conftest import FIXTURES

pytestmark = pytest.mark.db

ITUNES = json.loads((FIXTURES / "itunes_search_hard_fork.json").read_text())
BUZZ = (FIXTURES / "rss_buzzcast_trimmed.xml").read_bytes()
EDGE = (FIXTURES / "rss_edge_cases.xml").read_bytes()
HARD_FORK_FEED = "https://feeds.simplecast.com/6HKOhNgS"
HARD_FORK_ID = podcast_id(HARD_FORK_FEED)
UNKNOWN = "0" * 64


def _mock_upstreams(feed: bytes = BUZZ) -> None:
    respx.get(ITUNES_SEARCH_URL).mock(return_value=httpx.Response(200, json=ITUNES))
    # Simulate a feed host redirect, as many do.
    respx.get(HARD_FORK_FEED).mock(
        return_value=httpx.Response(301, headers={"Location": "https://moved.example/feed.xml"})
    )
    respx.get("https://moved.example/feed.xml").mock(return_value=httpx.Response(200, content=feed))


@respx.mock
async def test_search_episodes_detail_flow(
    client: httpx.AsyncClient, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    _mock_upstreams()

    resp = await client.get("/search", params={"q": "hard fork"})
    assert resp.status_code == 200
    search = SearchResponse.model_validate(resp.json())
    assert search.query == "hard fork"
    assert [h.title for h in search.results][:1] == ["Hard Fork"]
    assert len(search.results) == 3  # hit without feedUrl dropped
    assert search.results[0].id == HARD_FORK_ID

    resp = await client.get(f"/podcasts/{HARD_FORK_ID}/episodes", params={"limit": 2})
    assert resp.status_code == 200
    listing = PodcastEpisodes.model_validate(resp.json())
    assert listing.podcast.title == "Hard Fork"
    assert listing.total == 3
    assert len(listing.episodes) == 2
    dates = [e.published_at for e in listing.episodes]
    assert dates == sorted(dates, reverse=True)  # newest first
    first = listing.episodes[0]
    assert first.id == episode_id(HARD_FORK_FEED, "Buzzsprout-20079447")
    assert first.has_feed_transcript is True
    assert first.status == "new"

    resp = await client.get(f"/episodes/{first.id}")
    assert resp.status_code == 200
    detail = EpisodeDetail.model_validate(resp.json())
    assert detail.title == first.title
    assert detail.podcast.id == HARD_FORK_ID
    assert detail.audio_url.startswith("https://")
    assert detail.status == "new" and detail.active_job_id is None
    assert [t.type for t in detail.transcript_tags][-1] == "text/vtt"
    assert detail.guid == "Buzzsprout-20079447"

    # All three episodes were stored, not only the `limit` returned.
    async with db_sessionmaker() as s:
        assert (await s.scalar(select(func.count()).select_from(Episode))) == 3
        assert (await s.scalar(select(func.count()).select_from(Podcast))) == 3


@respx.mock
async def test_repeat_listing_is_idempotent_and_ids_stable(
    client: httpx.AsyncClient, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    _mock_upstreams(EDGE)
    await client.get("/search", params={"q": "hard fork"})
    first = (await client.get(f"/podcasts/{HARD_FORK_ID}/episodes")).json()
    second = (await client.get(f"/podcasts/{HARD_FORK_ID}/episodes")).json()
    assert [e["id"] for e in first["episodes"]] == [e["id"] for e in second["episodes"]]
    assert first["total"] == 5
    # undated episode sorts last
    assert first["episodes"][-1]["title"] == "Undated episode"
    # episode artwork falls back to the podcast's
    no_art = next(e for e in first["episodes"] if e["title"] == "No guid, MM:SS")
    assert no_art["artwork_url"] == ITUNES["results"][0]["artworkUrl600"]
    async with db_sessionmaker() as s:
        assert (await s.scalar(select(func.count()).select_from(Episode))) == 5


async def test_unknown_ids_404(client: httpx.AsyncClient) -> None:
    for path in (f"/podcasts/{UNKNOWN}/episodes", f"/episodes/{UNKNOWN}"):
        resp = await client.get(path)
        assert resp.status_code == 404, path
        assert ErrorResponse.model_validate(resp.json()).code == "not_found"


async def test_malformed_ids_422(client: httpx.AsyncClient) -> None:
    assert (await client.get("/episodes/not-a-hash")).status_code == 422
    assert (await client.get("/search", params={"q": ""})).status_code == 422


@pytest.mark.parametrize(
    "side_effect",
    [httpx.ReadTimeout("slow"), httpx.Response(500), httpx.Response(200, content=b"nope")],
)
@respx.mock
async def test_search_upstream_failure_is_clean_502(
    client: httpx.AsyncClient, side_effect: object
) -> None:
    route = respx.get(ITUNES_SEARCH_URL)
    if isinstance(side_effect, httpx.Response):
        route.mock(return_value=side_effect)
    else:
        route.mock(side_effect=side_effect)
    resp = await client.get("/search", params={"q": "x"})
    assert resp.status_code == 502
    body = ErrorResponse.model_validate(resp.json())
    assert body.code == "upstream_error"


@respx.mock
async def test_feed_failure_is_clean_502(client: httpx.AsyncClient) -> None:
    respx.get(ITUNES_SEARCH_URL).mock(return_value=httpx.Response(200, json=ITUNES))
    respx.get(HARD_FORK_FEED).mock(return_value=httpx.Response(410))
    await client.get("/search", params={"q": "hard fork"})
    resp = await client.get(f"/podcasts/{HARD_FORK_ID}/episodes")
    assert resp.status_code == 502
    assert resp.json()["code"] == "upstream_error"


@respx.mock
async def test_cors_allows_local_web_origin_on_real_route(client: httpx.AsyncClient) -> None:
    respx.get(ITUNES_SEARCH_URL).mock(return_value=httpx.Response(200, json=ITUNES))
    preflight = await client.options(
        "/search",
        headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"},
    )
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "http://localhost:3000"
    resp = await client.get(
        "/search", params={"q": "hard fork"}, headers={"Origin": "http://localhost:3000"}
    )
    assert resp.headers["access-control-allow-origin"] == "http://localhost:3000"
    evil = await client.get(
        "/search", params={"q": "x"}, headers={"Origin": "https://evil.example"}
    )
    assert "access-control-allow-origin" not in evil.headers


EP = "a" * 64
ANSWER_ROUTES = [
    ("POST", "/episodes/{ep}/ask", {"question": "What is this about?"}),
    ("GET", "/episodes/{ep}/artifacts/tldr", None),
    ("POST", "/episodes/{ep}/chat", {"message": "What is this about?"}),
]


@pytest.mark.parametrize(("method", "path", "body"), ANSWER_ROUTES)
async def test_answer_routes_404_unknown_and_409_not_ready(
    client: httpx.AsyncClient,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    method: str,
    path: str,
    body: dict | None,
) -> None:
    """Unknown episode -> 404; episode without transcript -> 409 (JSON, before any stream)."""
    from tests.fakes import BARE_EPISODE_ID, seed_answer_episode

    resp = await client.request(method, path.format(ep=UNKNOWN), json=body)
    assert resp.status_code == 404
    assert ErrorResponse.model_validate(resp.json()).code == "not_found"

    await seed_answer_episode(db_sessionmaker)
    resp = await client.request(method, path.format(ep=BARE_EPISODE_ID), json=body)
    assert resp.status_code == 409
    assert ErrorResponse.model_validate(resp.json()).code == "not_ready"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", f"/episodes/{EP}/ingest"),
        ("GET", "/jobs/00000000-0000-0000-0000-000000000000"),
        ("GET", "/jobs/00000000-0000-0000-0000-000000000000/events"),
        ("GET", "/jobs/not-a-uuid"),
    ],
)
async def test_ingest_and_job_routes_404_for_unknown_ids(
    client: httpx.AsyncClient, method: str, path: str
) -> None:
    """Implemented in M0-S4 (no longer 501): unknown episode/job -> 404 ErrorResponse."""
    resp = await client.request(method, path)
    assert resp.status_code == 404
    assert ErrorResponse.model_validate(resp.json()).code == "not_found"


async def test_request_bodies_are_validated(client: httpx.AsyncClient) -> None:
    assert (await client.post(f"/episodes/{EP}/ask", json={"question": ""})).status_code == 422
    too_long = {"question": "x" * 2001}
    assert (await client.post(f"/episodes/{EP}/ask", json=too_long)).status_code == 422
    assert (await client.get(f"/episodes/{EP}/artifacts/poem")).status_code == 422
    assert (await client.post(f"/episodes/{EP}/chat", json={"message": ""})).status_code == 422
    assert (await client.post(f"/episodes/{EP}/chat", json={})).status_code == 422


def test_app_fixture_type(app: FastAPI) -> None:
    assert app.title == "PodderSum API"


# --- episode chat (M0-S6) ----------------------------------------------------


def _chat_reply(*claims: tuple[str, int]) -> str:
    return "\n".join(json.dumps({"text": t, "segment_ids": [sid]}) for t, sid in claims)


async def _post_chat(client: httpx.AsyncClient, ep: str, message: str) -> list[tuple[str, dict]]:
    from tests.test_answer_routes import parse_sse

    resp = await client.post(f"/episodes/{ep}/chat", json={"message": message})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    return parse_sse(resp.text)


async def test_chat_sse_frames_match_contract(
    app: FastAPI, client: httpx.AsyncClient, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    from tests.fakes import ANSWER_EPISODE_ID, fake_answer_gateway, seed_answer_episode
    from tests.test_answer_routes import EVENT_ORDER

    segs = await seed_answer_episode(db_sessionmaker)
    app.state.gateway, provider = fake_answer_gateway(
        [_chat_reply(("Charge more.", segs[1].id), ("Invented.", 999_999))]
    )
    frames = await _post_chat(client, ANSWER_EPISODE_ID, "What should founders charge?")
    assert all(name == data["event"] for name, data in frames)
    names = " ".join(name for name, _ in frames)
    assert EVENT_ORDER.match(names), names
    answer = next(d for n, d in frames if n == "answer")["answer"]
    assert [c["text"] for c in answer["claims"]] == ["Charge more."]
    assert answer["claims"][0]["citations"][0]["start_ms"] == segs[1].start_ms
    assert len(provider.calls) == 1


async def test_chat_model_failure_streams_error_and_stores_nothing(
    app: FastAPI, client: httpx.AsyncClient, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    from tests.fakes import ANSWER_EPISODE_ID, fake_answer_gateway, seed_answer_episode
    from tests.test_answer_routes import EVENT_ORDER

    await seed_answer_episode(db_sessionmaker)
    app.state.gateway, _ = fake_answer_gateway([_chat_reply(("Invented.", 999_999))])
    frames = await _post_chat(client, ANSWER_EPISODE_ID, "Anything?")
    names = " ".join(name for name, _ in frames)
    assert EVENT_ORDER.match(names) and "error" in names, names
    assert (await client.get(f"/episodes/{ANSWER_EPISODE_ID}/chat")).json() == []


async def test_chat_history_404_unknown_and_empty_for_new_episode(
    client: httpx.AsyncClient, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    from tests.fakes import BARE_EPISODE_ID, seed_answer_episode

    resp = await client.get(f"/episodes/{UNKNOWN}/chat")
    assert resp.status_code == 404
    assert ErrorResponse.model_validate(resp.json()).code == "not_found"

    await seed_answer_episode(db_sessionmaker)
    resp = await client.get(f"/episodes/{BARE_EPISODE_ID}/chat")
    assert resp.status_code == 200 and resp.json() == []


async def test_chat_history_oldest_first_and_survives_restart(
    app: FastAPI,
    client: httpx.AsyncClient,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    db_schema: str,
) -> None:
    from agent import checkpointer as cp
    from api.main import create_app
    from api.schemas import ChatMessage, ChatRole
    from core.settings import LOCAL_DATABASE_URL, Settings
    from db.session import get_session
    from tests.fakes import ANSWER_EPISODE_ID, fake_answer_gateway, seed_answer_episode

    segs = await seed_answer_episode(db_sessionmaker)
    app.state.gateway, _ = fake_answer_gateway(
        [
            _chat_reply(("Founders undercharge.", segs[1].id)),
            _chat_reply(("The top price converted well.", segs[2].id)),
        ]
    )
    await _post_chat(client, ANSWER_EPISODE_ID, "First question?")
    await _post_chat(client, ANSWER_EPISODE_ID, "Follow-up question?")

    def check(history: list[ChatMessage]) -> None:
        assert [m.role for m in history] == [
            ChatRole.user,
            ChatRole.assistant,
            ChatRole.user,
            ChatRole.assistant,
        ]
        assert [history[0].text, history[2].text] == ["First question?", "Follow-up question?"]
        assert [c.text for c in history[3].claims] == ["The top price converted well."]
        assert [m.created_at for m in history] == sorted(m.created_at for m in history)

    resp = await client.get(f"/episodes/{ANSWER_EPISODE_ID}/chat")
    assert resp.status_code == 200
    check([ChatMessage.model_validate(m) for m in resp.json()])

    # "Restart": drop the saver and its pool, then boot a second app whose own lifespan
    # opens a fresh checkpointer (no preset) on the same database schema.
    await cp.close_checkpointer()
    settings = Settings(
        _env_file=None,
        GEMINI_API_KEY="x",
        DATABASE_URL=f"{LOCAL_DATABASE_URL}?options=-csearch_path%3D{db_schema}",
    )
    second = create_app(settings)
    second.state.sessionmaker = db_sessionmaker
    second.dependency_overrides[get_session] = app.dependency_overrides[get_session]
    async with second.router.lifespan_context(second):
        assert second.state.checkpointer is not None
        assert second.state.checkpointer is not app.state.checkpointer
        transport = httpx.ASGITransport(app=second)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c2:
            resp = await c2.get(f"/episodes/{ANSWER_EPISODE_ID}/chat")
    assert resp.status_code == 200
    check([ChatMessage.model_validate(m) for m in resp.json()])
    assert second.state.checkpointer is None  # opened by the lifespan, closed by it
