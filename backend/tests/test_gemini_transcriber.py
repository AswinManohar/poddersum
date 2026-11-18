"""Gemini transcriber on a fake provider with recorded-style responses: real download
(mock transport), real ffmpeg chunking, speaker carry-over, offsets, cost, cleanup."""

from __future__ import annotations

import base64
from pathlib import Path

import httpx
import pytest

from ingest.audio import Chunk
from ingest.gemini_transcriber import (
    PROMPT_VERSION,
    GeminiTranscriber,
    Turn,
    parse_offset_ms,
    turns_to_cues,
)
from ingest.transcriber import AudioSource, TranscriptionError
from llm import LLMAuthError
from llm.fake import FakeReply
from tests.conftest import FIXTURES
from tests.fakes import fake_gateway
from tests.test_audio import is_empty_dir, make_tone_silence_tone

AUDIO_URL = "https://tracker.example/redirect/ep.mp3"
CDN_URL = "https://cdn.example/ep.mp3"


def _reply(name: str) -> FakeReply:
    return FakeReply((FIXTURES / name).read_text(), tokens_in=1000, tokens_out=200, audio_in=800)


@pytest.fixture(scope="module")
def mp3_bytes(tmp_path_factory: pytest.TempPathFactory) -> bytes:
    import asyncio

    path = tmp_path_factory.mktemp("audio") / "ep.mp3"
    asyncio.run(make_tone_silence_tone(path))
    return path.read_bytes()


class AudioServer:
    """Mock transport: redirect from the tracker, then serve the mp3; counts downloads."""

    def __init__(self, body: bytes, status: int = 200) -> None:
        self.body, self.status, self.downloads = body, status, 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if str(request.url) == AUDIO_URL:
            return httpx.Response(302, headers={"Location": CDN_URL})
        self.downloads += 1
        return httpx.Response(self.status, content=self.body)


def _transcriber(gateway, server: AudioServer, tmp_root: Path, **kw) -> GeminiTranscriber:
    http = httpx.AsyncClient(transport=httpx.MockTransport(server))
    return GeminiTranscriber(gateway, http, chunk_ms=4_000, tmp_root=tmp_root, **kw)


AUDIO = AudioSource(episode_id="e" * 64, audio_url=AUDIO_URL, title="Test Pod 1")


async def test_two_chunks_speakers_offsets_cost(mp3_bytes: bytes, tmp_path: Path) -> None:
    gateway, fake = fake_gateway(
        [_reply("stt_response_chunk1.json"), _reply("stt_response_chunk2.json")]
    )
    server = AudioServer(mp3_bytes)
    events: list[tuple[str, float, str | None]] = []

    async def report(step, fraction, message):
        events.append((step.value, fraction, message))

    tr = _transcriber(gateway, server, tmp_path)
    result = await tr.transcribe(AUDIO, report)

    assert server.downloads == 1
    assert len(fake.calls) == 2
    # audio sent inline as mp3, prompt versioned, metadata attributed
    first_content = fake.calls[0]["messages"][0]["content"]
    assert first_content[1]["file"]["file_data"].startswith("data:audio/mp3;base64,")
    sent = base64.b64decode(first_content[1]["file"]["file_data"].split(",", 1)[1])
    assert sent[:3] == b"ID3" or sent[0] == 0xFF
    assert "introduce themselves" in first_content[0]["text"]
    # speaker names from chunk 1 are passed to chunk 2
    second_prompt = fake.calls[1]["messages"][0]["content"][0]["text"]
    assert "- Ana Lopez (host)" in second_prompt and "- Bob Smith (guest)" in second_prompt
    assert "part 2 of 2" in second_prompt

    chunk2_start = result.raw["chunks"][1]["start_ms"]
    assert 3_800 <= chunk2_start <= 4_200  # cut in the silence, not at exactly 4 s
    c1 = [c for c in result.cues if c.start_ms < chunk2_start]
    c2 = [c for c in result.cues if c.start_ms >= chunk2_start]
    assert [c.start_ms for c in c1] == [0, 1000, 2000]
    assert c1[0].speaker == "Ana Lopez" and c1[1].speaker == "Bob Smith"
    rel = [c.start_ms - chunk2_start for c in c2]
    end2 = result.raw["chunks"][1]["end_ms"] - chunk2_start
    assert rel == [0, 2000, 2000, end2, end2]  # backwards -> repaired; 99:00 -> clamped
    assert c2[-1].speaker is None
    starts = [c.start_ms for c in result.cues]
    assert starts == sorted(starts)
    assert all(c.end_ms >= c.start_ms for c in result.cues)
    assert result.cues[-1].end_ms <= result.duration_ms

    # accounting summed over chunks; model from LLMResult
    per_call_cost = result.raw["chunks"][0]["cost_usd"]
    assert per_call_cost > 0
    assert result.cost_usd == pytest.approx(2 * per_call_cost)
    assert (result.tokens_in, result.tokens_out) == (2000, 400)
    assert result.source == "gemini" and result.model == result.raw["chunks"][0]["model"]
    assert result.model and "fake" in result.model
    assert result.language == "en" and result.raw["prompt_version"] == PROMPT_VERSION
    assert [s["label"] for s in result.raw["speakers"]] == ["Ana Lopez", "Bob Smith"]

    steps = [e[0] for e in events]
    assert steps.index("download_audio") < steps.index("transcribe")
    assert any(m and "chunk 2 of 2" in m for _, _, m in events)
    assert is_empty_dir(tmp_path)  # temp audio deleted


async def test_auth_error_propagates_and_cleans_up(mp3_bytes: bytes, tmp_path: Path) -> None:
    gateway, fake = fake_gateway([RuntimeError('GeminiException {"reason": "API_KEY_INVALID"}')])
    tr = _transcriber(gateway, AudioServer(mp3_bytes), tmp_path)
    with pytest.raises(LLMAuthError) as err:
        await tr.transcribe(AUDIO)
    assert "API_KEY_INVALID" in str(err.value) and "test-dummy-key" not in str(err.value)
    assert len(fake.calls) == 1  # chunk 1 failed: later chunks never sent
    assert is_empty_dir(tmp_path)


async def test_failure_in_later_chunk_cleans_up(mp3_bytes: bytes, tmp_path: Path) -> None:
    gateway, _ = fake_gateway([_reply("stt_response_chunk1.json"), "this is not json"])
    tr = _transcriber(gateway, AudioServer(mp3_bytes), tmp_path)
    with pytest.raises(Exception):  # noqa: B017 - structured-output error from the gateway
        await tr.transcribe(AUDIO)
    assert is_empty_dir(tmp_path)


async def test_download_errors_are_clean(mp3_bytes: bytes, tmp_path: Path) -> None:
    gateway, fake = fake_gateway(["unused"])
    with pytest.raises(TranscriptionError, match="HTTP 404"):
        await _transcriber(gateway, AudioServer(b"", status=404), tmp_path).transcribe(AUDIO)
    with pytest.raises(TranscriptionError, match="too large"):
        await _transcriber(gateway, AudioServer(mp3_bytes), tmp_path, max_bytes=1000).transcribe(
            AUDIO
        )
    assert fake.calls == []
    assert is_empty_dir(tmp_path)


@pytest.mark.parametrize(
    ("value", "ms"),
    [("00:00", 0), ("1:05", 65_000), ("01:02:03", 3_723_000), ("12", 12_000), ("12.5", 12_500),
     ("03:04.250", 184_250), (7.25, 7_250), ("soon", None), ("", None), (None, None)],
)  # fmt: skip
def test_parse_offset(value, ms) -> None:
    assert parse_offset_ms(value) == ms


def test_turns_to_cues_offsets_by_chunk_start() -> None:
    chunk = Chunk(1, 900_000, 1_800_000)
    turns = [Turn(start="00:00", speaker="A", text="x"), Turn(start="10:00", speaker="B", text="y")]
    cues = turns_to_cues(turns, chunk)
    assert [(c.start_ms, c.end_ms, c.speaker) for c in cues] == [
        (900_000, 1_500_000, "A"),
        (1_500_000, 1_800_000, "B"),
    ]


def test_turns_to_cues_detects_episode_absolute_times() -> None:
    chunk = Chunk(1, 900_000, 1_800_000)
    turns = [Turn(start="15:05", text="x"), Turn(start="29:00", text="y")]
    assert [c.start_ms for c in turns_to_cues(turns, chunk)] == [905_000, 1_740_000]


def test_turns_to_cues_skips_empty_text() -> None:
    cues = turns_to_cues(
        [Turn(start="00:01", text="  "), Turn(start="00:02", text="ok")], Chunk(0, 0, 5000)
    )
    assert [(c.start_ms, c.text) for c in cues] == [(2000, "ok")]
