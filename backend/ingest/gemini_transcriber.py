"""Source 2: audio transcription through the model gateway (task ``stt_cheap``).

Flow (all temp files live in one ``TemporaryDirectory``, removed in every case):

1. stream-download the enclosure (redirects followed, size-capped);
2. bundled ffmpeg: duration, ``silencedetect`` (only when more than one chunk is needed),
   cut points near each 15-minute boundary (:func:`ingest.audio.plan_chunks`);
3. each chunk re-encoded to mono 16 kHz 32 kbps mp3 (~3.6 MB per 15 min) and sent inline
   to ``Gateway.transcribe_audio`` with a JSON schema of speaker turns. Chunk 1 runs first
   and names the speakers from the intro; later chunks get those names as context and run
   with bounded concurrency;
4. turn timestamps (relative to the chunk) are repaired (clamped, made monotonic) and
   offset by the chunk's real start (:func:`turns_to_cues`).

Tokens and cost are summed over chunks; the model id comes from ``LLMResult.model``.
"""

from __future__ import annotations

import asyncio
import logging
import re
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field

from api.schemas import JobStep
from ingest import audio as audio_mod
from ingest.audio import (
    CHUNK_MIME,
    CHUNK_MS,
    MAX_AUDIO_BYTES,
    MIN_LAST_CHUNK_MS,
    SILENCE_WINDOW_MS,
    Chunk,
)
from ingest.transcriber import (
    AudioSource,
    Cue,
    Reporter,
    TranscriptionError,
    TranscriptResult,
    no_report,
)
from llm import CallMetadata, Gateway, LLMResult

log = logging.getLogger(__name__)

STT_TASK = "stt_cheap"
FEATURE = "ingest_transcribe"
DEFAULT_CONCURRENCY = 3
ABSOLUTE_TOLERANCE_MS = 5_000

PROMPT_VERSION = "transcribe-v1"
TRANSCRIBE_PROMPT = """\
You are transcribing part {part} of {parts} of a podcast episode{title}.
This audio clip is {clip_len} long. Transcribe ALL speech in it verbatim, in the spoken
language, as a list of speaker turns in order.

For every turn give:
- "start": when the turn starts, as MM:SS measured from the START OF THIS CLIP
  (00:00 is the first moment of this clip, never the episode time);
- "speaker": the speaker's real name when known, otherwise a stable label such as
  "Speaker 1", "Speaker 2";
- "text": exactly what was said in that turn (no summaries, no stage directions).
Start a new turn whenever the speaker changes; split a monologue into turns of at most
about one minute. Skip music and silence.

Also return "speakers": everyone who speaks in this clip, with "label" (exactly the value
you used in "speaker") and "role" (host, co-host, guest, ad, or unknown), and "language"
(an ISO 639-1 code).
{speaker_context}"""

FIRST_CHUNK_CONTEXT = """
This is the start of the episode: hosts and guests usually introduce themselves here.
Use their real names as speaker labels whenever the audio states them."""

LATER_CHUNK_CONTEXT = """
Speakers identified earlier in this episode (reuse exactly these labels for the same
voices; add new labels only for new voices):
{speakers}"""


class SpeakerInfo(BaseModel):
    label: str
    role: str = ""


class Turn(BaseModel):
    start: str = Field(description="MM:SS from the start of this clip")
    speaker: str = ""
    text: str


class ChunkTranscript(BaseModel):
    language: str = ""
    speakers: list[SpeakerInfo] = Field(default_factory=list)
    turns: list[Turn] = Field(default_factory=list)


# --------------------------------------------------------------------------- pure helpers

_CLOCK = re.compile(r"^\s*(\d+(?::\d{1,2}){0,2})(?:[.,](\d+))?\s*s?\s*$")


def parse_offset_ms(value: Any) -> int | None:
    """``MM:SS``, ``HH:MM:SS``, ``SS``, ``SS.s`` (optionally with fraction) -> ms."""
    if isinstance(value, int | float) and not isinstance(value, bool):
        return max(0, int(round(float(value) * 1000)))
    if not isinstance(value, str):
        return None
    m = _CLOCK.match(value)
    if not m:
        return None
    seconds = 0
    for p in m.group(1).split(":"):
        seconds = seconds * 60 + int(p)
    frac = m.group(2)
    ms = int((frac + "000")[:3]) if frac else 0
    return seconds * 1000 + ms


def _fmt_clock(ms: int) -> str:
    s = ms // 1000
    return f"{s // 60}:{s % 60:02d}"


def turns_to_cues(turns: Sequence[Turn], chunk: Chunk) -> list[Cue]:
    """Chunk-relative turns -> absolute cues. Unparseable starts reuse the previous one;
    starts are clamped to the chunk and made non-decreasing; each turn ends where the next
    begins (the last at the chunk end). Turns that clearly use episode-absolute times
    (all at or after the chunk start, some beyond the chunk length, and all fitting the
    chunk once shifted) are shifted back."""
    dur = chunk.duration_ms
    raw = [parse_offset_ms(t.start) for t in turns]
    known = [s for s in raw if s is not None]
    if (
        chunk.start_ms > 2 * ABSOLUTE_TOLERANCE_MS
        and known
        and min(known) >= chunk.start_ms - ABSOLUTE_TOLERANCE_MS
        and max(known) > dur + ABSOLUTE_TOLERANCE_MS
        and max(known) - chunk.start_ms <= dur + ABSOLUTE_TOLERANCE_MS
    ):
        raw = [None if s is None else s - chunk.start_ms for s in raw]

    rel: list[tuple[int, Turn]] = []
    prev = 0
    for turn, start in zip(turns, raw, strict=True):
        if not turn.text.strip():
            continue
        s = prev if start is None else start
        s = min(max(s, prev, 0), dur)
        prev = s
        rel.append((s, turn))

    cues: list[Cue] = []
    for i, (s, turn) in enumerate(rel):
        e = rel[i + 1][0] if i + 1 < len(rel) else dur
        e = max(e, s)
        cues.append(
            Cue(
                chunk.start_ms + s,
                chunk.start_ms + e,
                " ".join(turn.text.split()),
                turn.speaker.strip() or None,
            )
        )
    return cues


def build_prompt(
    chunk: Chunk, parts: int, known_speakers: Sequence[SpeakerInfo], title: str | None
) -> str:
    if chunk.index == 0:
        context = FIRST_CHUNK_CONTEXT
    elif known_speakers:
        lines = "\n".join(
            f"- {s.label}" + (f" ({s.role})" if s.role else "") for s in known_speakers
        )
        context = LATER_CHUNK_CONTEXT.format(speakers=lines)
    else:
        context = ""
    return TRANSCRIBE_PROMPT.format(
        part=chunk.index + 1,
        parts=parts,
        title=f' titled "{title}"' if title else "",
        clip_len=_fmt_clock(chunk.duration_ms),
        speaker_context=context,
    )


def merge_speakers(known: Sequence[SpeakerInfo], new: Sequence[SpeakerInfo]) -> list[SpeakerInfo]:
    out = list(known)
    seen = {s.label.strip().lower() for s in known}
    for s in new:
        key = s.label.strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(s)
    return out


@dataclass(slots=True)
class ChunkResult:
    chunk: Chunk
    cues: list[Cue]
    output: ChunkTranscript
    llm: LLMResult


# --------------------------------------------------------------------------- transcriber


class GeminiTranscriber:
    """:class:`ingest.transcriber.Transcriber` on the model gateway (``stt_cheap``)."""

    source = "gemini"

    def __init__(
        self,
        gateway: Gateway,
        http: httpx.AsyncClient,
        *,
        task: str = STT_TASK,
        concurrency: int = DEFAULT_CONCURRENCY,
        chunk_ms: int = CHUNK_MS,
        max_bytes: int = MAX_AUDIO_BYTES,
        tmp_root: str | Path | None = None,
    ) -> None:
        self.gateway = gateway
        self.http = http
        self.task = task
        self.concurrency = max(1, concurrency)
        self.chunk_ms = chunk_ms
        self.max_bytes = max_bytes
        self.tmp_root = str(tmp_root) if tmp_root else None

    def plan(self, duration_ms: int, silences: Sequence[tuple[int, int]]) -> list[Chunk]:
        """Chunk plan; the silence window and minimum tail scale down for tiny test chunks
        (15-minute chunks: +/-30 s window, 60 s minimum tail)."""
        return audio_mod.plan_chunks(
            duration_ms,
            silences,
            chunk_ms=self.chunk_ms,
            window_ms=min(SILENCE_WINDOW_MS, self.chunk_ms // 4),
            min_last_ms=min(MIN_LAST_CHUNK_MS, self.chunk_ms // 8),
        )

    async def transcribe(
        self, audio: AudioSource, report: Reporter = no_report
    ) -> TranscriptResult:
        with tempfile.TemporaryDirectory(
            prefix="poddersum-audio-", dir=self.tmp_root, ignore_cleanup_errors=True
        ) as tmp:
            workdir = Path(tmp)
            src = workdir / "source.audio"
            await report(JobStep.download_audio, 0.0, "Downloading audio")

            async def on_download(fraction: float) -> None:
                await report(JobStep.download_audio, min(fraction, 1.0) * 0.8, None)

            size = await audio_mod.download_audio(
                self.http, audio.audio_url, src, max_bytes=self.max_bytes, on_progress=on_download
            )
            duration_ms = await audio_mod.probe_duration_ms(src)
            silences: list[tuple[int, int]] = []
            if len(self.plan(duration_ms, ())) > 1:
                await report(JobStep.download_audio, 0.85, "Finding silences to cut at")
                silences = await audio_mod.detect_silences(src)
            chunks = self.plan(duration_ms, silences)
            if not chunks:
                raise TranscriptionError("audio is empty")
            await report(
                JobStep.download_audio,
                1.0,
                f"Downloaded {size / 2**20:.1f} MB, {_fmt_clock(duration_ms)} of audio, "
                f"{len(chunks)} chunk(s)",
            )
            results = await self._transcribe_chunks(src, workdir, chunks, audio, report)
        # temp dir is gone here (also on any exception above)
        return self._combine(results, audio, duration_ms)

    async def _transcribe_chunks(
        self,
        src: Path,
        workdir: Path,
        chunks: list[Chunk],
        audio: AudioSource,
        report: Reporter,
    ) -> list[ChunkResult]:
        total = len(chunks)
        done = 0

        async def progress(msg: str) -> None:
            await report(JobStep.transcribe, done / total, msg)

        await progress(f"Transcribing chunk 1 of {total}")
        first = await self.transcribe_chunk(src, workdir, chunks[0], total, [], audio)
        done = 1
        speakers = merge_speakers([], first.output.speakers)
        results = [first]
        if total > 1:
            await progress(f"Transcribed chunk 1 of {total}")
            sem = asyncio.Semaphore(self.concurrency)

            async def one(chunk: Chunk) -> ChunkResult:
                nonlocal done
                async with sem:
                    res = await self.transcribe_chunk(src, workdir, chunk, total, speakers, audio)
                done += 1
                await progress(f"Transcribed chunk {chunk.index + 1} of {total}")
                return res

            try:
                async with asyncio.TaskGroup() as tg:
                    tasks = [tg.create_task(one(c)) for c in chunks[1:]]
            except BaseExceptionGroup as group:
                raise _first_error(group) from None
            results += [t.result() for t in tasks]
        await report(JobStep.transcribe, 1.0, f"Transcribed {total} chunk(s)")
        return results

    async def transcribe_chunk(
        self,
        src: Path,
        workdir: Path,
        chunk: Chunk,
        parts: int,
        known_speakers: Sequence[SpeakerInfo],
        audio: AudioSource,
    ) -> ChunkResult:
        path = workdir / f"chunk-{chunk.index:03d}.mp3"
        try:
            data = await audio_mod.cut_chunk(src, path, chunk.start_ms, chunk.end_ms)
        finally:
            path.unlink(missing_ok=True)
        prompt = build_prompt(chunk, parts, known_speakers, audio.title)
        result = await self.gateway.transcribe_audio(
            self.task,
            data,
            CHUNK_MIME,
            prompt,
            metadata=CallMetadata(
                feature=FEATURE,
                prompt_version=PROMPT_VERSION,
                episode_id=audio.episode_id,
                extra={"chunk": chunk.index, "chunks": parts},
            ),
            schema=ChunkTranscript,
        )
        output: ChunkTranscript = result.parsed
        return ChunkResult(chunk, turns_to_cues(output.turns, chunk), output, result)

    def _combine(
        self, results: list[ChunkResult], audio: AudioSource, duration_ms: int
    ) -> TranscriptResult:
        results = sorted(results, key=lambda r: r.chunk.index)
        cues = [c for r in results for c in r.cues]
        if not cues:
            raise TranscriptionError("the transcription returned no speech")
        speakers: list[SpeakerInfo] = []
        for r in results:
            speakers = merge_speakers(speakers, r.output.speakers)
        models = sorted({r.llm.model for r in results})
        language = next((r.output.language for r in results if r.output.language), None)
        return TranscriptResult(
            source=self.source,
            cues=cues,
            source_url=audio.audio_url,
            model=", ".join(models),
            cost_usd=round(sum(r.llm.cost_usd for r in results), 6),
            tokens_in=sum(r.llm.tokens_in for r in results),
            tokens_out=sum(r.llm.tokens_out for r in results),
            language=language,
            duration_ms=duration_ms,
            raw={
                "prompt_version": PROMPT_VERSION,
                "speakers": [s.model_dump() for s in speakers],
                "chunks": [
                    {
                        "index": r.chunk.index,
                        "start_ms": r.chunk.start_ms,
                        "end_ms": r.chunk.end_ms,
                        "model": r.llm.model,
                        "tokens_in": r.llm.tokens_in,
                        "tokens_out": r.llm.tokens_out,
                        "cost_usd": r.llm.cost_usd,
                        "output": r.output.model_dump(),
                    }
                    for r in results
                ],
            },
        )


def _first_error(group: BaseExceptionGroup) -> BaseException:
    for exc in group.exceptions:
        if isinstance(exc, BaseExceptionGroup):
            return _first_error(exc)
        return exc
    return group
