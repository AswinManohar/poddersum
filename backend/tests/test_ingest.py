"""Ingest end to end against the real (throwaway-schema) Postgres: feed path, Gemini path
with a fake provider, idempotency, concurrency, advisory lock, failure + retry, restart
recovery, SSE replay and episode status."""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

import httpx
import pytest
import respx
from fastapi import FastAPI
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.schemas import EpisodeDetail, JobEvent, JobStatus
from db import queries
from db.models import Episode, Job, Podcast, Segment, Transcript
from ingest.gemini_transcriber import GeminiTranscriber
from ingest.locks import LockNotAcquired, episode_lock, episode_lock_key
from ingest.runner import INTERRUPTED_BY_RESTART, IngestRunner
from ingest.transcriber import AudioSource, Cue, TranscriptResult
from llm import LLMAuthError
from llm.fake import FakeReply
from tests.conftest import FIXTURES, PG_SKIP_REASON, PG_UP
from tests.fakes import fake_gateway
from tests.test_audio import is_empty_dir, make_tone_silence_tone

pytestmark = [pytest.mark.db, pytest.mark.skipif(not PG_UP, reason=PG_SKIP_REASON)]

POD = "0" * 64
EP_FEED = "1" * 64
EP_AUDIO = "2" * 64
VTT_URL = "https://feed.example/ep1/transcript.vtt"
HTML_URL = "https://feed.example/ep1/transcript.html"
AUDIO_URL = "https://audio.example/ep2.mp3"
AUTH_MSG = "Gemini rejected GEMINI_API_KEY (API_KEY_INVALID); replace the key in .env"


class FakeTranscriber:
    """Counts calls; optionally waits on a gate (to keep a job running) or fails."""

    source = "gemini"

    def __init__(self, *, gate: asyncio.Event | None = None, error: Exception | None = None):
        self.calls = 0
        self.gate = gate
        self.error = error

    async def transcribe(self, audio: AudioSource, report=None) -> TranscriptResult:
        self.calls += 1
        if report:
            from api.schemas import JobStep

            await report(JobStep.download_audio, 0.0, "Downloading audio")
            await report(JobStep.transcribe, 0.5, "Transcribing chunk 1 of 1")
        if self.gate:
            await self.gate.wait()
        if self.error:
            raise self.error
        return TranscriptResult(
            source="gemini",
            cues=[Cue(0, 4000, "Hello there.", "Ana"), Cue(4000, 9000, "Hi Ana.", "Bob")],
            source_url=audio.audio_url,
            model="fake-stt-1",
            cost_usd=0.0123,
            tokens_in=1500,
            tokens_out=300,
            duration_ms=9000,
            raw={"fake": True},
        )


@pytest.fixture
async def seeded(db_sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    async with db_sessionmaker() as s:
        s.add(Podcast(id=POD, feed_url="https://feed.example/rss", title="Test Pod"))
        await s.flush()
        s.add_all(
            [
                Episode(
                    id=EP_FEED,
                    podcast_id=POD,
                    guid="g1",
                    title="Feed episode",
                    audio_url="https://audio.example/ep1.mp3",
                    transcript_tags=[
                        {"url": HTML_URL, "type": "text/html", "language": "en", "rel": None},
                        {"url": VTT_URL, "type": "text/vtt", "language": "en", "rel": None},
                    ],
                ),
                Episode(
                    id=EP_AUDIO,
                    podcast_id=POD,
                    guid="g2",
                    title="Audio-only episode",
                    audio_url=AUDIO_URL,
                    transcript_tags=[],
                ),
            ]
        )
        await s.commit()


def use_runner(app: FastAPI, factory, **kw) -> IngestRunner:
    runner = IngestRunner(app.state.sessionmaker, app.state.http_client, factory, **kw)
    app.state.ingest_runner = runner
    return runner


async def wait_idle(runner: IngestRunner) -> None:
    async with asyncio.timeout(15):
        while runner.tasks:
            await asyncio.gather(*list(runner.tasks.values()), return_exceptions=True)


def parse_sse(body: str) -> list[tuple[str, int, JobEvent]]:
    frames = []
    for block in body.replace("\r\n", "\n").split("\n\n"):
        fields: dict[str, str] = {}
        for line in block.split("\n"):
            if line and not line.startswith(":") and ":" in line:
                k, _, v = line.partition(":")
                fields[k] = v.lstrip(" ")
        if "data" in fields:
            frames.append(
                (fields["event"], int(fields["id"]), JobEvent.model_validate_json(fields["data"]))
            )
    return frames


async def post_ingest(client: httpx.AsyncClient, ep: str) -> JobStatus:
    resp = await client.post(f"/episodes/{ep}/ingest")
    assert resp.status_code == 202, resp.text
    return JobStatus.model_validate(resp.json())


async def count(maker, model) -> int:
    async with maker() as s:
        return (await s.execute(select(func.count()).select_from(model))).scalar_one()


# --------------------------------------------------------------------------- feed path


@respx.mock(assert_all_called=False)
async def test_feed_transcript_end_to_end_then_idempotent(
    app: FastAPI, client: httpx.AsyncClient, db_sessionmaker, seeded, respx_mock
) -> None:
    vtt = respx_mock.get(VTT_URL).mock(
        return_value=httpx.Response(
            200, content=(FIXTURES / "transcript_buzzcast.vtt").read_bytes()
        )
    )
    html = respx_mock.get(HTML_URL).mock(return_value=httpx.Response(200, text="<p>x</p>"))
    stt = FakeTranscriber()
    runner = use_runner(app, lambda: stt)

    job = await post_ingest(client, EP_FEED)
    assert job.state == "queued" and job.step == "queued" and job.episode_id == EP_FEED
    await wait_idle(runner)

    # SSE: late connect replays everything and closes after the terminal event
    resp = await client.get(f"/jobs/{job.job_id}/events")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    frames = parse_sse(resp.text)
    assert all(name == "job" for name, _, _ in frames)
    assert [seq for _, seq, _ in frames] == list(range(1, len(frames) + 1))
    assert all(ev.seq == seq and ev.job_id == job.job_id for _, seq, ev in frames)
    steps = [ev.step.value for _, _, ev in frames]
    assert steps[0] == "queued" and steps[-1] == "done"
    assert "fetch_transcript" in steps and "segment" in steps and "store" in steps
    assert "download_audio" not in steps and "transcribe" not in steps
    assert frames[-1][2].state == "succeeded" and frames[-1][2].progress == 1.0
    progress = [ev.progress for _, _, ev in frames]
    assert progress == sorted(progress)

    status = JobStatus.model_validate((await client.get(f"/jobs/{job.job_id}")).json())
    assert status.state == "succeeded" and status.step == "done" and status.finished_at

    async with db_sessionmaker() as s:
        t = await queries.get_transcript(s, EP_FEED)
        segs = await queries.get_episode_segments(s, EP_FEED)
        tsv_hits = (
            await s.execute(
                text(
                    "SELECT count(*) FROM segments "
                    "WHERE tsv @@ plainto_tsquery('english', 'podcast')"
                )
            )
        ).scalar_one()
    assert t is not None and t.source == "feed" and t.source_url == VTT_URL
    assert float(t.cost_usd) == 0 and t.model is None and t.raw["format"] == "vtt"
    assert len(segs) > 0 and segs[0].idx == 0 and segs[0].speaker == "Jordan"
    assert all(s.end_ms >= s.start_ms for s in segs) and tsv_hits > 0
    assert stt.calls == 0 and not html.called

    detail = EpisodeDetail.model_validate((await client.get(f"/episodes/{EP_FEED}")).json())
    assert detail.status == "ready" and detail.active_job_id is None

    # second POST: same finished job, no download, no model call, no new job
    calls_before = vtt.call_count
    again = await post_ingest(client, EP_FEED)
    assert again.job_id == job.job_id and again.state == "succeeded"
    await wait_idle(runner)
    assert vtt.call_count == calls_before and stt.calls == 0
    assert await count(db_sessionmaker, Job) == 1
    assert await count(db_sessionmaker, Transcript) == 1


async def test_unknown_episode_404(client: httpx.AsyncClient, seeded) -> None:
    resp = await client.post(f"/episodes/{'f' * 64}/ingest")
    assert resp.status_code == 404 and resp.json()["code"] == "not_found"


# --------------------------------------------------------------------------- concurrency


async def test_simultaneous_posts_make_one_job_and_one_transcription(
    app: FastAPI, client: httpx.AsyncClient, db_sessionmaker, seeded
) -> None:
    gate = asyncio.Event()
    stt = FakeTranscriber(gate=gate)
    runner = use_runner(app, lambda: stt)

    jobs = await asyncio.gather(*[post_ingest(client, EP_AUDIO) for _ in range(6)])
    assert len({j.job_id for j in jobs}) == 1
    # while running: status processing with the active job id
    await asyncio.sleep(0.2)
    detail = EpisodeDetail.model_validate((await client.get(f"/episodes/{EP_AUDIO}")).json())
    assert detail.status == "processing" and detail.active_job_id == jobs[0].job_id
    assert (await post_ingest(client, EP_AUDIO)).job_id == jobs[0].job_id

    gate.set()
    await wait_idle(runner)
    assert stt.calls == 1
    assert await count(db_sessionmaker, Job) == 1
    async with db_sessionmaker() as s:
        t = await queries.get_transcript(s, EP_AUDIO)
    assert t is not None and t.source == "gemini" and t.model == "fake-stt-1"
    assert float(t.cost_usd) == pytest.approx(0.0123) and (t.tokens_in, t.tokens_out) == (1500, 300)


async def test_concurrent_enqueue_on_separate_runners_one_job(db_sessionmaker, seeded) -> None:
    """Two runners (as if two API processes) racing: the partial unique index wins."""
    async with httpx.AsyncClient() as http:
        gate = asyncio.Event()
        stt = FakeTranscriber(gate=gate)
        r1 = IngestRunner(db_sessionmaker, http, lambda: stt)
        r2 = IngestRunner(db_sessionmaker, http, lambda: stt)
        a, b = await asyncio.gather(r1.enqueue(EP_AUDIO), r2.enqueue(EP_AUDIO))
        assert a.job_id == b.job_id
        gate.set()
        await wait_idle(r1)
        await wait_idle(r2)
    assert stt.calls == 1


async def test_advisory_lock_contention(db_sessionmaker, seeded) -> None:
    engine = db_sessionmaker.kw["bind"]
    assert episode_lock_key(EP_AUDIO) == episode_lock_key(EP_AUDIO) != episode_lock_key(EP_FEED)
    async with episode_lock(engine, EP_AUDIO):
        with pytest.raises(LockNotAcquired):
            async with episode_lock(engine, EP_AUDIO, wait_s=0.2, poll_s=0.05):
                pass
        async with episode_lock(engine, EP_FEED):  # other episodes unaffected
            pass
        # a job that cannot get the lock fails cleanly and does no work
        async with httpx.AsyncClient() as http:
            stt = FakeTranscriber()
            runner = IngestRunner(db_sessionmaker, http, lambda: stt, lock_wait_s=0.2)
            job = await runner.enqueue(EP_AUDIO)
            await wait_idle(runner)
        assert stt.calls == 0
        async with db_sessionmaker() as s:
            failed = await queries.get_job(s, uuid.UUID(job.job_id))
        assert failed.state == "failed" and "another worker" in failed.error
    async with episode_lock(engine, EP_AUDIO):  # released afterwards
        pass


# --------------------------------------------------------------------------- failures


async def test_failure_then_retry(
    app: FastAPI, client: httpx.AsyncClient, db_sessionmaker, seeded
) -> None:
    stt = FakeTranscriber(error=LLMAuthError(AUTH_MSG))
    runner = use_runner(app, lambda: stt)
    first = await post_ingest(client, EP_AUDIO)
    await wait_idle(runner)

    failed = JobStatus.model_validate((await client.get(f"/jobs/{first.job_id}")).json())
    assert failed.state == "failed" and failed.error == AUTH_MSG and failed.finished_at
    assert failed.step == "transcribe"
    frames = parse_sse((await client.get(f"/jobs/{first.job_id}/events")).text)
    assert frames[-1][2].state == "failed" and frames[-1][2].message == AUTH_MSG
    detail = EpisodeDetail.model_validate((await client.get(f"/episodes/{EP_AUDIO}")).json())
    assert detail.status == "failed" and detail.active_job_id is None
    assert await count(db_sessionmaker, Transcript) == 0

    stt.error = None
    second = await post_ingest(client, EP_AUDIO)
    assert second.job_id != first.job_id and second.state == "queued"
    await wait_idle(runner)
    assert (
        JobStatus.model_validate((await client.get(f"/jobs/{second.job_id}")).json()).state
        == "succeeded"
    )
    async with db_sessionmaker() as s:
        jobs = {j.id: j for j in (await s.execute(select(Job))).scalars()}
    assert jobs[uuid.UUID(second.job_id)].attempts == 2
    assert stt.calls == 2


@pytest.fixture(scope="module")
def mp3_bytes(tmp_path_factory: pytest.TempPathFactory) -> bytes:
    path = tmp_path_factory.mktemp("audio") / "ep.mp3"
    asyncio.run(make_tone_silence_tone(path))
    return path.read_bytes()


def _audio_http(body: bytes, counter: list[int]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        counter.append(1)
        return httpx.Response(200, content=body)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_gemini_path_through_runner_records_model_and_cost(
    app: FastAPI, client: httpx.AsyncClient, db_sessionmaker, seeded, mp3_bytes, tmp_path: Path
) -> None:
    reply = FakeReply(
        (FIXTURES / "stt_response_chunk1.json").read_text(), tokens_in=900, tokens_out=100
    )
    gateway, fake = fake_gateway([reply])
    downloads: list[int] = []
    http = _audio_http(mp3_bytes, downloads)
    runner = use_runner(app, lambda: GeminiTranscriber(gateway, http, tmp_root=tmp_path))
    job = await post_ingest(client, EP_AUDIO)
    await wait_idle(runner)
    frames = parse_sse((await client.get(f"/jobs/{job.job_id}/events")).text)
    steps = [ev.step.value for _, _, ev in frames]
    assert steps[0] == "queued" and steps[-1] == "done" and frames[-1][2].state == "succeeded"
    for step in ("fetch_transcript", "download_audio", "transcribe", "segment", "store"):
        assert step in steps
    async with db_sessionmaker() as s:
        t = await queries.get_transcript(s, EP_AUDIO)
        segs = await queries.get_episode_segments(s, EP_AUDIO)
    assert t.source == "gemini" and t.model and "fake" in t.model and float(t.cost_usd) > 0
    assert t.source_url == AUDIO_URL and t.duration_ms and t.raw["chunks"]
    assert [s.speaker for s in segs] == ["Ana Lopez", "Bob Smith", "Ana Lopez"]
    assert is_empty_dir(tmp_path)

    # process once, serve many: second POST -> no download, no model call
    again = await post_ingest(client, EP_AUDIO)
    await wait_idle(runner)
    assert again.job_id == job.job_id and len(downloads) == 1 and len(fake.calls) == 1
    await http.aclose()


async def test_gemini_auth_failure_through_runner_cleans_temp(
    app: FastAPI, client: httpx.AsyncClient, seeded, mp3_bytes, tmp_path: Path
) -> None:
    gateway, _ = fake_gateway([RuntimeError('{"reason": "API_KEY_INVALID"}')])
    http = _audio_http(mp3_bytes, [])
    runner = use_runner(app, lambda: GeminiTranscriber(gateway, http, tmp_root=tmp_path))
    job = await post_ingest(client, EP_AUDIO)
    await wait_idle(runner)
    status = JobStatus.model_validate((await client.get(f"/jobs/{job.job_id}")).json())
    assert status.state == "failed" and status.error == AUTH_MSG
    assert "test-dummy-key" not in (status.error or "")
    assert is_empty_dir(tmp_path)
    await http.aclose()


async def test_unexpected_crash_is_reported_without_details(
    app: FastAPI, client: httpx.AsyncClient, seeded
) -> None:
    stt = FakeTranscriber(error=KeyError("secret-ish internal detail"))
    runner = use_runner(app, lambda: stt)
    job = await post_ingest(client, EP_AUDIO)
    await wait_idle(runner)
    status = JobStatus.model_validate((await client.get(f"/jobs/{job.job_id}")).json())
    assert status.state == "failed" and status.error == "internal error (KeyError)"


# --------------------------------------------------------------------------- restart


async def _stale_running_job(maker, episode_id: str) -> uuid.UUID:
    async with maker() as s:
        job = await queries.create_job(s, episode_id)
        await queries.append_job_event(
            s, job.id, state="running", step="transcribe", progress=0.4, message="Transcribing"
        )
        await s.commit()
        return job.id


async def test_restart_recovery_fails_orphans_and_retry_works(
    app: FastAPI, client: httpx.AsyncClient, db_sessionmaker, seeded
) -> None:
    job_id = await _stale_running_job(db_sessionmaker, EP_AUDIO)
    async with httpx.AsyncClient() as http:
        fresh = IngestRunner(db_sessionmaker, http, FakeTranscriber)  # "new process"
        assert await fresh.recover_interrupted() == 1
        assert await fresh.recover_interrupted() == 0
    status = JobStatus.model_validate((await client.get(f"/jobs/{job_id}")).json())
    assert status.state == "failed" and status.error == INTERRUPTED_BY_RESTART
    assert status.step == "transcribe"
    frames = parse_sse((await client.get(f"/jobs/{job_id}/events")).text)
    assert [ev.state.value for _, _, ev in frames] == ["queued", "running", "failed"]

    runner = use_runner(app, FakeTranscriber)
    retry = await post_ingest(client, EP_AUDIO)
    assert retry.job_id != str(job_id)
    await wait_idle(runner)
    assert (
        JobStatus.model_validate((await client.get(f"/jobs/{retry.job_id}")).json()).state
        == "succeeded"
    )


async def test_restart_recovery_skips_jobs_whose_lock_is_held(db_sessionmaker, seeded) -> None:
    job_id = await _stale_running_job(db_sessionmaker, EP_AUDIO)
    async with episode_lock(db_sessionmaker.kw["bind"], EP_AUDIO), httpx.AsyncClient() as http:
        assert await IngestRunner(db_sessionmaker, http, FakeTranscriber).recover_interrupted() == 0
    async with db_sessionmaker() as s:
        assert (await queries.get_job(s, job_id)).state == "running"


async def test_app_startup_runs_recovery(settings, db_sessionmaker, seeded, checkpointer) -> None:
    from api.main import create_app

    job_id = await _stale_running_job(db_sessionmaker, EP_AUDIO)
    application = create_app(settings)
    application.state.sessionmaker = db_sessionmaker
    application.state.checkpointer = checkpointer
    application.state.transcriber_factory = FakeTranscriber
    async with application.router.lifespan_context(application):
        pass
    async with db_sessionmaker() as s:
        job = await queries.get_job(s, job_id)
    assert job.state == "failed" and job.error == INTERRUPTED_BY_RESTART


# --------------------------------------------------------------------------- SSE


async def test_sse_last_event_id_and_live_follow(
    app: FastAPI, client: httpx.AsyncClient, seeded
) -> None:
    gate = asyncio.Event()
    runner = use_runner(app, lambda: FakeTranscriber(gate=gate))
    job = await post_ingest(client, EP_AUDIO)
    await asyncio.sleep(0.3)  # job is now running, blocked on the gate

    live = asyncio.create_task(client.get(f"/jobs/{job.job_id}/events"))
    await asyncio.sleep(0.3)
    assert not live.done()  # following live, not closed
    gate.set()
    frames = parse_sse((await asyncio.wait_for(live, 10)).text)
    await wait_idle(runner)
    assert frames[-1][2].state == "succeeded"
    total = len(frames)

    resumed = parse_sse(
        (await client.get(f"/jobs/{job.job_id}/events", headers={"Last-Event-ID": "2"})).text
    )
    assert [seq for _, seq, _ in resumed] == list(range(3, total + 1))

    after_end = await client.get(
        f"/jobs/{job.job_id}/events", headers={"Last-Event-ID": str(total)}
    )
    assert after_end.status_code == 200 and parse_sse(after_end.text) == []

    garbage = parse_sse(
        (await client.get(f"/jobs/{job.job_id}/events", headers={"Last-Event-ID": "x"})).text
    )
    assert len(garbage) == total


async def test_sse_works_without_in_process_notifier(db_sessionmaker, seeded) -> None:
    """Events written by another 'process' are found by polling Postgres alone."""
    from api.routes.jobs import stream_job_events

    async with db_sessionmaker() as s:
        job = await queries.create_job(s, EP_AUDIO)
        await s.commit()

    async def writer() -> None:
        await asyncio.sleep(0.15)
        async with db_sessionmaker() as s:
            await queries.append_job_event(
                s, job.id, state="succeeded", step="done", progress=1.0, message="ok"
            )
            await s.commit()

    w = asyncio.create_task(writer())
    got = [
        json.loads(f["data"])
        async for f in stream_job_events(db_sessionmaker, None, job.id, 0, poll_s=0.05)
    ]
    await w
    assert [e["seq"] for e in got] == [1, 2] and got[-1]["state"] == "succeeded"


# --------------------------------------------------------------------------- status


async def test_episode_processing_mapping_one_query(db_sessionmaker, seeded) -> None:
    async with db_sessionmaker() as s:
        assert (await queries.episode_processing(s, [EP_FEED]))[EP_FEED].status == "new"
        job = await queries.create_job(s, EP_FEED)
        await s.commit()
        p = await queries.episode_processing(s, [EP_FEED, EP_AUDIO])
        assert p[EP_FEED].status == "processing" and p[EP_FEED].active_job_id == str(job.id)
        assert p[EP_AUDIO].status == "new"
        await queries.append_job_event(
            s, job.id, state="failed", step="fetch_transcript", progress=0.1, message="x", error="x"
        )
        await s.commit()
        assert (await queries.episode_processing(s, [EP_FEED]))[EP_FEED].status == "failed"
        res = TranscriptResult(source="feed", cues=[])
        from ingest.segmenter import SegmentData

        await queries.store_transcript(s, EP_FEED, res, [SegmentData(0, 0, 10, None, "hi")])
        await s.commit()
        assert (await queries.episode_processing(s, [EP_FEED]))[EP_FEED].status == "ready"
        assert await queries.episode_processing(s, []) == {}
        seg = (await s.execute(select(Segment))).scalar_one()
        assert isinstance(seg.id, int) and seg.id >= 1


@respx.mock
async def test_episode_list_shows_status(
    client: httpx.AsyncClient, db_sessionmaker, seeded
) -> None:
    """The list route maps status for its page in one query (here: one ready episode)."""
    from discovery.feed import parse_feed

    rss = (FIXTURES / "rss_buzzcast_trimmed.xml").read_bytes()
    async with db_sessionmaker() as s:
        pod = await s.get(Podcast, POD)
        pod.feed_url = "https://rss.buzzsprout.com/231452.rss"
        await s.commit()
    respx.get("https://rss.buzzsprout.com/231452.rss").mock(
        return_value=httpx.Response(200, content=rss)
    )
    feed = parse_feed(rss, "https://rss.buzzsprout.com/231452.rss")
    target = feed.episodes[0].id
    first = (await client.get(f"/podcasts/{POD}/episodes")).json()
    assert all(e["status"] == "new" for e in first["episodes"])
    async with db_sessionmaker() as s:
        from ingest.segmenter import SegmentData

        await queries.store_transcript(
            s, target, TranscriptResult(source="feed", cues=[]), [SegmentData(0, 0, 1, None, "x")]
        )
        await s.commit()
    listed = (await client.get(f"/podcasts/{POD}/episodes")).json()["episodes"]
    by_id = {e["id"]: e["status"] for e in listed}
    assert by_id[target] == "ready"
    assert sum(1 for v in by_id.values() if v == "ready") == 1
