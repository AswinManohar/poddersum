"""Audio download, ffmpeg helpers and chunk planning for audio transcription.

ffmpeg is the binary bundled with ``imageio-ffmpeg`` (no system install needed) and always
runs through ``asyncio.create_subprocess_exec`` so the event loop never blocks. A cancelled
caller kills the ffmpeg process before the error propagates.

Chunking: episodes are cut into ~15-minute chunks. Each cut point is the middle of the
detected silence nearest to the 15-minute boundary, if one lies within +/-30 s; otherwise
the exact boundary. ``plan_chunks`` is a pure function (see tests).
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import httpx

from ingest.transcriber import TranscriptionError

log = logging.getLogger(__name__)

CHUNK_MS = 15 * 60 * 1000
SILENCE_WINDOW_MS = 30_000
MIN_LAST_CHUNK_MS = 60_000  # a shorter tail is merged into the previous chunk
MAX_AUDIO_BYTES = 500 * 1024 * 1024
DOWNLOAD_TIMEOUT = httpx.Timeout(60.0, connect=10.0)
DOWNLOAD_MAX_REDIRECTS = 10  # enclosures often bounce through several trackers
SILENCE_NOISE_DB = -30
SILENCE_MIN_S = 0.5
CHUNK_BITRATE = "32k"
CHUNK_SAMPLE_RATE = 16_000
CHUNK_MIME = "audio/mp3"


def make_download_client(user_agent: str) -> httpx.AsyncClient:
    """HTTP client for enclosures: more redirects allowed than the feed client."""
    return httpx.AsyncClient(
        headers={"User-Agent": user_agent},
        max_redirects=DOWNLOAD_MAX_REDIRECTS,
        timeout=DOWNLOAD_TIMEOUT,
    )


@lru_cache
def ffmpeg_exe() -> str:
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


# --------------------------------------------------------------------------- download


async def download_audio(
    http: httpx.AsyncClient,
    url: str,
    dest: Path,
    *,
    max_bytes: int = MAX_AUDIO_BYTES,
    on_progress: Callable[[float], Awaitable[None]] | None = None,
) -> int:
    """Stream ``url`` to ``dest`` following redirects; returns bytes written."""
    try:
        async with http.stream("GET", url, follow_redirects=True, timeout=DOWNLOAD_TIMEOUT) as resp:
            if resp.status_code >= 400:
                raise TranscriptionError(f"audio download failed (HTTP {resp.status_code})")
            declared = resp.headers.get("content-length")
            total = int(declared) if declared and declared.isdigit() else None
            if total and total > max_bytes:
                raise TranscriptionError(f"audio file too large (> {max_bytes // 2**20} MB)")
            written = 0
            last_reported = 0
            with dest.open("wb") as fh:
                async for chunk in resp.aiter_bytes(256 * 1024):
                    written += len(chunk)
                    if written > max_bytes:
                        raise TranscriptionError(
                            f"audio file too large (> {max_bytes // 2**20} MB)"
                        )
                    await asyncio.to_thread(fh.write, chunk)
                    if on_progress and total and written - last_reported >= 2**20:
                        last_reported = written
                        await on_progress(written / total)
    except httpx.TooManyRedirects as exc:
        raise TranscriptionError("audio download failed (too many redirects)") from exc
    except httpx.HTTPError as exc:
        raise TranscriptionError(f"audio download failed ({type(exc).__name__})") from exc
    if written == 0:
        raise TranscriptionError("audio download returned no data")
    return written


# --------------------------------------------------------------------------- ffmpeg


async def run_ffmpeg(args: Sequence[str], *, timeout_s: float = 1800) -> tuple[int, str]:
    """Run bundled ffmpeg with ``args``; returns (exit code, stderr text)."""
    proc = await asyncio.create_subprocess_exec(
        ffmpeg_exe(),
        "-hide_banner",
        "-nostdin",
        *args,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        async with asyncio.timeout(timeout_s):
            _, stderr = await proc.communicate()
    except BaseException:
        if proc.returncode is None:
            proc.kill()
            await asyncio.shield(proc.wait())
        raise
    return proc.returncode or 0, stderr.decode("utf-8", "replace")


_DURATION = re.compile(r"Duration:\s*(\d+):(\d{2}):(\d{2}(?:\.\d+)?)")
_TIME = re.compile(r"time=(\d+):(\d{2}):(\d{2}(?:\.\d+)?)")
_SIL_START = re.compile(r"silence_start:\s*(-?\d+(?:\.\d+)?)")
_SIL_END = re.compile(r"silence_end:\s*(-?\d+(?:\.\d+)?)")


def _hms_ms(h: str, m: str, s: str) -> int:
    return int(round((int(h) * 3600 + int(m) * 60 + float(s)) * 1000))


def parse_duration_ms(stderr: str) -> int | None:
    m = _DURATION.search(stderr)
    if m:
        return _hms_ms(*m.groups())
    times = _TIME.findall(stderr)
    return _hms_ms(*times[-1]) if times else None


def parse_silences(stderr: str) -> list[tuple[int, int]]:
    """``silencedetect`` output -> [(start_ms, end_ms)]."""
    out: list[tuple[int, int]] = []
    start: float | None = None
    for line in stderr.splitlines():
        if (m := _SIL_START.search(line)) is not None:
            start = max(0.0, float(m.group(1)))
        elif (m := _SIL_END.search(line)) is not None and start is not None:
            out.append((int(round(start * 1000)), int(round(float(m.group(1)) * 1000))))
            start = None
    return out


async def probe_duration_ms(path: Path) -> int:
    """Duration from the container header; full decode as a fallback."""
    _, stderr = await run_ffmpeg(["-i", str(path)], timeout_s=120)
    duration = parse_duration_ms(stderr)
    if duration is None:
        _, stderr = await run_ffmpeg(["-i", str(path), "-vn", "-f", "null", "-"])
        duration = parse_duration_ms(stderr)
    if not duration:
        raise TranscriptionError("could not read the audio duration (unsupported file?)")
    return duration


async def detect_silences(path: Path) -> list[tuple[int, int]]:
    code, stderr = await run_ffmpeg(
        [
            "-i",
            str(path),
            "-vn",
            "-af",
            f"silencedetect=noise={SILENCE_NOISE_DB}dB:d={SILENCE_MIN_S}",
            "-f",
            "null",
            "-",
        ]
    )
    if code != 0:
        log.warning("silencedetect failed (exit %s); cutting at exact boundaries", code)
        return []
    return parse_silences(stderr)


async def cut_chunk(src: Path, dest: Path, start_ms: int, end_ms: int) -> bytes:
    """Cut [start, end) and re-encode to mono 16 kHz ~32 kbps mp3; returns the bytes."""
    code, stderr = await run_ffmpeg(
        [
            "-ss",
            f"{start_ms / 1000:.3f}",
            "-t",
            f"{(end_ms - start_ms) / 1000:.3f}",
            "-i",
            str(src),
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(CHUNK_SAMPLE_RATE),
            "-c:a",
            "libmp3lame",
            "-b:a",
            CHUNK_BITRATE,
            "-loglevel",
            "error",
            "-y",
            str(dest),
        ],
        timeout_s=600,
    )
    if code != 0 or not await asyncio.to_thread(dest.exists):
        log.warning("ffmpeg cut failed: %s", stderr[-300:])
        raise TranscriptionError("audio could not be re-encoded for transcription")
    return await asyncio.to_thread(dest.read_bytes)


# --------------------------------------------------------------------------- planning


@dataclass(frozen=True, slots=True)
class Chunk:
    index: int
    start_ms: int
    end_ms: int

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


def choose_cut(
    target_ms: int, silences: Sequence[tuple[int, int]], window_ms: int = SILENCE_WINDOW_MS
) -> int:
    """Middle of the silence nearest ``target_ms`` within ``window_ms``, else ``target_ms``."""
    best: int | None = None
    for start, end in silences:
        mid = (start + end) // 2
        if abs(mid - target_ms) <= window_ms and (
            best is None or abs(mid - target_ms) < abs(best - target_ms)
        ):
            best = mid
    return target_ms if best is None else best


def plan_chunks(
    duration_ms: int,
    silences: Sequence[tuple[int, int]] = (),
    *,
    chunk_ms: int = CHUNK_MS,
    window_ms: int = SILENCE_WINDOW_MS,
    min_last_ms: int = MIN_LAST_CHUNK_MS,
) -> list[Chunk]:
    """Contiguous chunks covering [0, duration_ms)."""
    if duration_ms <= 0:
        return []
    cuts: list[int] = []
    target = chunk_ms
    while target < duration_ms:
        cut = choose_cut(target, silences, window_ms)
        prev = cuts[-1] if cuts else 0
        if prev < cut < duration_ms:
            cuts.append(cut)
        target += chunk_ms
    while cuts and duration_ms - cuts[-1] < min_last_ms:
        cuts.pop()
    bounds = [0, *cuts, duration_ms]
    return [Chunk(i, bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1)]


def needs_silence_scan(duration_ms: int, chunk_ms: int = CHUNK_MS) -> bool:
    """Short episodes are a single chunk; no need to decode them for silences."""
    return len(plan_chunks(duration_ms, (), chunk_ms=chunk_ms)) > 1
