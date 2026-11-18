"""A fake PodderSum API (contract v0) serving the recorded fixtures in ``fixtures/api``.

Stateful per process: ingest moves an episode to ``processing``, the job SSE stream replays
``job_events.json`` and then marks it ``ready``. Scenarios are keyed by episode position
in ``episodes.json`` (newest first):

0  "The Science of Second Chances"   new   -> job succeeds
1  "Sleep, Memory..."                 ready (feed transcript)
2  "#212: The Price of Certainty"     new   -> job stalls after one event (timeout tests)
3  "Rethinking Rest: Part 1"          new   -> job fails
4  "Rethinking Rest: Part 2"          ready

Toggles (``POST /_fake/config``): ``{"llm_down": true}`` makes presets/ask/chat fail like a
backend without a working model key (503 / ``llm_auth``); ``{"transcript_route": false}``
removes the transcript route, as in today's real backend (contract v0 has none).

``GET /episodes/{id}/transcript`` is a PROPOSED route (API gap): ``?start_ms=&end_ms=&limit=``
-> ``{episode_id, segments: [{id, idx, start_ms, end_ms, speaker, text}], total_segments,
next_start_ms}``; 404 unknown episode, 409 ``not_ready``.

Run standalone: ``python tests/fake_api.py --port 8010 [--llm-down]``.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import threading
import uuid
from pathlib import Path
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

FIXTURES = Path(__file__).parent / "fixtures" / "api"
DEFAULT_LLM_DOWN = False
LLM_DOWN_BODY = {"detail": "The model provider rejected the API key.", "code": "llm_auth"}
EVENT_DELAY_S = 0.02


def load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


class FakeState:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.search = load("search.json")
        self.listing = load("episodes.json")
        self.podcast = self.listing["podcast"]
        self.episodes = self.listing["episodes"]
        self.status = {e["id"]: "new" for e in self.episodes}
        self.status[self.episodes[1]["id"]] = "ready"
        self.status[self.episodes[4]["id"]] = "ready"
        self.jobs: dict[str, dict[str, Any]] = {}
        self.chat: dict[str, list[dict[str, Any]]] = {}
        self.requests: list[dict[str, Any]] = []
        self.known_podcasts: set[str] = set()
        self.llm_down = DEFAULT_LLM_DOWN
        self.transcript_route = True

    def scenario(self, episode_id: str) -> int:
        return next(i for i, e in enumerate(self.episodes) if e["id"] == episode_id)

    def ref(self, ep: dict[str, Any]) -> dict[str, Any]:
        return {**ep, "status": self.status[ep["id"]]}

    def detail(self, episode_id: str) -> dict[str, Any] | None:
        ep = next((e for e in self.episodes if e["id"] == episode_id), None)
        if ep is None:
            return None
        idx = self.scenario(episode_id)
        active = next(
            (j["job_id"] for j in self.jobs.values()
             if j["episode_id"] == episode_id and j["state"] in ("queued", "running")),
            None,
        )  # fmt: skip
        tags = (
            [{"url": "https://audio.example.com/hb/sleep.vtt", "type": "text/vtt", "language": "en",
              "rel": "captions"}]
            if ep["has_feed_transcript"] else []
        )  # fmt: skip
        return {
            **self.ref(ep),
            "podcast": self.podcast,
            "guid": f"fixture-guid-{idx}",
            "audio_url": f"https://audio.example.com/hb/episode-{idx}.mp3?src=feed",
            "audio_type": "audio/mpeg",
            "transcript_tags": tags,
            "active_job_id": active,
        }


STATE = FakeState()


def error(status: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse({"detail": detail, "code": code}, status_code=status)


def sse(frames: list[tuple[str, str | None, Any]], *, stall: bool = False, after=None) -> StreamingResponse:
    async def gen():
        for event, frame_id, data in frames:
            await asyncio.sleep(EVENT_DELAY_S)
            head = f"event: {event}\n" + (f"id: {frame_id}\n" if frame_id else "")
            yield f"{head}data: {json.dumps(data)}\n\n"
        if after is not None:
            after()
        while stall:
            await asyncio.sleep(0.2)
            yield ": ping\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


async def record(request: Request) -> None:
    body = (await request.body()).decode() or None
    STATE.requests.append(
        {
            "method": request.method,
            "path": request.url.path,
            "query": dict(request.query_params),
            "authorization": request.headers.get("authorization"),
            "user_agent": request.headers.get("user-agent"),
            "body": json.loads(body) if body else None,
        }
    )


async def healthz(request: Request) -> Response:
    return JSONResponse({"status": "ok", "env": "fake", "db": "ok"})


async def search(request: Request) -> Response:
    await record(request)
    q = request.query_params.get("q", "")
    limit = int(request.query_params.get("limit", 20))
    results = STATE.search["results"] if "hidden" in q.lower() or "brain" in q.lower() else []
    STATE.known_podcasts.update(r["id"] for r in results)
    return JSONResponse({"query": q.strip(), "results": results[:limit]})


async def podcast_episodes(request: Request) -> Response:
    await record(request)
    pid = request.path_params["podcast_id"]
    if pid != STATE.podcast["id"]:
        return error(404, "not_found", "Unknown podcast. Search for it first.")
    limit = int(request.query_params.get("limit", 50))
    eps = [STATE.ref(e) for e in STATE.episodes[:limit]]
    return JSONResponse({"podcast": STATE.podcast, "episodes": eps, "total": len(STATE.episodes)})


async def episode(request: Request) -> Response:
    await record(request)
    found = STATE.detail(request.path_params["episode_id"])
    return JSONResponse(found) if found else error(404, "not_found", "Unknown episode.")


async def ingest(request: Request) -> Response:
    await record(request)
    eid = request.path_params["episode_id"]
    if STATE.detail(eid) is None:
        return error(404, "not_found", "Unknown episode.")
    for job in STATE.jobs.values():
        if job["episode_id"] == eid and (
            job["state"] in ("queued", "running") or STATE.status[eid] == "ready"
        ):
            return JSONResponse(job, status_code=202)
    job = copy.deepcopy(load("ingest_job.json"))
    job["job_id"] = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{eid}/{len(STATE.jobs)}"))
    job["episode_id"] = eid
    if STATE.status[eid] == "ready":
        job.update(state="succeeded", step="done", progress=1.0, message="Transcript ready")
    else:
        STATE.status[eid] = "processing"
    STATE.jobs[job["job_id"]] = job
    return JSONResponse(job, status_code=202)


async def get_job(request: Request) -> Response:
    await record(request)
    job = STATE.jobs.get(request.path_params["job_id"])
    return JSONResponse(job) if job else error(404, "not_found", "Unknown job.")


async def job_events(request: Request) -> Response:
    await record(request)
    job = STATE.jobs.get(request.path_params["job_id"])
    if job is None:
        return error(404, "not_found", "Unknown job.")
    eid = job["episode_id"]
    scenario = STATE.scenario(eid)
    events = [dict(e, job_id=job["job_id"]) for e in load("job_events.json")]
    if scenario == 2:  # stalls mid-transcription
        job.update(state="running", step="transcribe", progress=0.35, message="Transcribing chunk 1 of 2")
        return sse([("job", str(events[1]["seq"]), {**events[1], "seq": 1})], stall=True)
    if scenario == 3:  # fails during transcription
        failed = {**events[1], "state": "failed", "message": "Audio download returned HTTP 403"}
        frames = [("job", "1", {**events[0], "seq": 1}), ("job", "2", {**failed, "seq": 2})]

        def fail() -> None:
            job.update(state="failed", step="transcribe", error="Audio download returned HTTP 403")
            STATE.status[eid] = "failed"

        return sse(frames, after=fail)

    def succeed() -> None:
        job.update(state="succeeded", step="done", progress=1.0, message="Transcript ready")
        STATE.status[eid] = "ready"

    return sse([("job", str(e["seq"]), e) for e in events], after=succeed)


def _answer_frames(name: str) -> list[tuple[str, str | None, Any]]:
    return [(e["event"], None, e) for e in load(name)]


async def ask(request: Request) -> Response:
    await record(request)
    eid = request.path_params["episode_id"]
    if STATE.detail(eid) is None:
        return error(404, "not_found", "Unknown episode.")
    if STATE.status[eid] != "ready":
        return error(409, "not_ready", "This episode has no transcript yet; ingest it first.")
    body = await request.json()
    if STATE.llm_down:
        return sse(_llm_down_frames())
    name = "ask_error_events.json" if "[fail]" in body.get("question", "") else "ask_events.json"
    return sse(_answer_frames(name))


async def chat(request: Request) -> Response:
    await record(request)
    eid = request.path_params["episode_id"]
    if STATE.detail(eid) is None:
        return error(404, "not_found", "Unknown episode.")
    if request.method == "GET":
        return JSONResponse(STATE.chat.get(eid, []))
    if STATE.status[eid] != "ready":
        return error(409, "not_ready", "This episode has no transcript yet; ingest it first.")
    body = await request.json()
    if STATE.llm_down:
        return sse(_llm_down_frames())
    frames = _answer_frames("chat_events.json")
    reply = next(e for e in load("chat_events.json") if e["event"] == "answer")["answer"]
    now = "2026-11-18T10:05:00Z"
    STATE.chat.setdefault(eid, []).extend(
        [
            {
                "id": str(uuid.uuid4()),
                "role": "user",
                "text": body["message"],
                "claims": [],
                "created_at": now,
            },
            {
                "id": str(uuid.uuid4()),
                "role": "assistant",
                "text": " ".join(c["text"] for c in reply["claims"]),
                "claims": reply["claims"],
                "created_at": now,
            },
        ]
    )
    return sse(frames)


async def artifact(request: Request) -> Response:
    await record(request)
    eid, kind = request.path_params["episode_id"], request.path_params["kind"]
    if STATE.detail(eid) is None:
        return error(404, "not_found", "Unknown episode.")
    if kind not in ("tldr", "takeaways", "verdict"):
        return JSONResponse({"detail": [{"loc": ["path", "kind"], "msg": "bad kind"}]}, status_code=422)
    if STATE.status[eid] != "ready":
        return error(409, "not_ready", "This episode has no transcript yet; ingest it first.")
    if STATE.llm_down:
        return JSONResponse(LLM_DOWN_BODY, status_code=503)
    data = load(f"artifact_{kind}.json")
    data["episode_id"] = eid
    return JSONResponse(data)


def _llm_down_frames() -> list[tuple[str, str | None, Any]]:
    events = [
        {"event": "status", "message": "Reading the transcript"},
        {"event": "error", "message": LLM_DOWN_BODY["detail"], "code": "llm_auth"},
        {"event": "done"},
    ]
    return [(e["event"], None, e) for e in events]


def episode_segments(episode_id: str) -> list[dict[str, Any]]:
    """Episode 0's recorded transcript; other episodes reuse it with offset ids."""
    scenario = STATE.scenario(episode_id)
    segs = load("transcript_ep0.json")["segments"]
    return [dict(seg, id=seg["id"] + 1000 * scenario) for seg in segs]


async def transcript(request: Request) -> Response:
    await record(request)
    if not STATE.transcript_route:
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    eid = request.path_params["episode_id"]
    if STATE.detail(eid) is None:
        return error(404, "not_found", "Unknown episode.")
    if STATE.status[eid] != "ready":
        return error(409, "not_ready", "This episode has no transcript yet; ingest it first.")
    q = request.query_params
    start = int(q.get("start_ms", 0))
    end = int(q["end_ms"]) if "end_ms" in q else None
    limit = int(q.get("limit", 500))
    segs = [
        s for s in episode_segments(eid) if s["start_ms"] >= start and (end is None or s["start_ms"] < end)
    ]
    page, rest = segs[:limit], segs[limit:]
    return JSONResponse(
        {
            "episode_id": eid,
            "segments": page,
            "total_segments": len(episode_segments(eid)),
            "next_start_ms": rest[0]["start_ms"] if rest else None,
        }
    )


async def fake_config(request: Request) -> Response:
    body = await request.json()
    STATE.llm_down = bool(body.get("llm_down", STATE.llm_down))
    STATE.transcript_route = bool(body.get("transcript_route", STATE.transcript_route))
    return JSONResponse({"llm_down": STATE.llm_down, "transcript_route": STATE.transcript_route})


async def fake_reset(request: Request) -> Response:
    STATE.reset()
    return JSONResponse({"ok": True})


async def fake_requests(request: Request) -> Response:
    return JSONResponse(STATE.requests)


ROUTES = [
    Route("/healthz", healthz),
    Route("/search", search),
    Route("/podcasts/{podcast_id}/episodes", podcast_episodes),
    Route("/episodes/{episode_id}", episode),
    Route("/episodes/{episode_id}/ingest", ingest, methods=["POST"]),
    Route("/jobs/{job_id}", get_job),
    Route("/jobs/{job_id}/events", job_events),
    Route("/episodes/{episode_id}/ask", ask, methods=["POST"]),
    Route("/episodes/{episode_id}/chat", chat, methods=["GET", "POST"]),
    Route("/episodes/{episode_id}/artifacts/{kind}", artifact),
    Route("/episodes/{episode_id}/transcript", transcript),
    Route("/_fake/config", fake_config, methods=["POST"]),
    Route("/_fake/reset", fake_reset, methods=["POST"]),
    Route("/_fake/requests", fake_requests),
]
app = Starlette(routes=ROUTES)


class FakeApiServer:
    """uvicorn in a background thread on a free port."""

    def __init__(self, port: int = 0) -> None:
        self.config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off")
        self.server = uvicorn.Server(self.config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def start(self) -> str:
        self.thread.start()
        while not self.server.started:
            threading.Event().wait(0.02)
        port = self.server.servers[0].sockets[0].getsockname()[1]
        self.url = f"http://127.0.0.1:{port}"
        return self.url

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8010)
    parser.add_argument("--llm-down", action="store_true", help="Start with the server model down.")
    args = parser.parse_args()
    STATE.llm_down = args.llm_down
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
