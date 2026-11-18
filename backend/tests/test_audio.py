"""Chunk planning (pure) and a real round trip through the bundled ffmpeg."""

from __future__ import annotations

from pathlib import Path

import pytest

from ingest import audio
from ingest.audio import CHUNK_MS, Chunk, choose_cut, plan_chunks
from ingest.transcriber import TranscriptionError

MIN = 60_000


def test_short_episode_is_one_chunk() -> None:
    assert plan_chunks(12 * MIN) == [Chunk(0, 0, 12 * MIN)]
    assert plan_chunks(CHUNK_MS) == [Chunk(0, 0, CHUNK_MS)]
    assert not audio.needs_silence_scan(14 * MIN)
    assert audio.needs_silence_scan(40 * MIN)


def test_empty_duration() -> None:
    assert plan_chunks(0) == []


def test_no_silence_cuts_at_exact_boundaries() -> None:
    chunks = plan_chunks(40 * MIN)
    assert [(c.start_ms, c.end_ms) for c in chunks] == [
        (0, 15 * MIN),
        (15 * MIN, 30 * MIN),
        (30 * MIN, 40 * MIN),
    ]


def test_cut_at_silence_nearest_boundary() -> None:
    silences = [
        (15 * MIN - 25_000, 15 * MIN - 24_000),  # mid -24.5 s
        (15 * MIN + 9_000, 15 * MIN + 11_000),  # mid +10 s (nearest)
        (30 * MIN + 45_000, 30 * MIN + 46_000),  # outside +/-30 s -> exact boundary
    ]
    chunks = plan_chunks(40 * MIN, silences)
    assert [c.start_ms for c in chunks] == [0, 15 * MIN + 10_000, 30 * MIN]
    assert chunks[-1].end_ms == 40 * MIN
    # contiguous coverage
    assert all(a.end_ms == b.start_ms for a, b in zip(chunks, chunks[1:], strict=False))


def test_tiny_tail_merges_into_previous_chunk() -> None:
    chunks = plan_chunks(30 * MIN + 20_000)
    assert [(c.start_ms, c.end_ms) for c in chunks] == [
        (0, 15 * MIN),
        (15 * MIN, 30 * MIN + 20_000),
    ]


def test_choose_cut_window() -> None:
    assert choose_cut(100_000, [(125_000, 135_000)]) == 130_000  # exactly 30 s away: ok
    assert choose_cut(100_000, [(126_000, 136_000)]) == 100_000  # 31 s away: ignored
    assert choose_cut(100_000, [(120_000, 130_000)]) == 125_000
    assert choose_cut(100_000, []) == 100_000


def test_offsets_with_custom_chunk_size() -> None:
    chunks = plan_chunks(10_000, [(3_900, 4_300)], chunk_ms=4_000, window_ms=500, min_last_ms=500)
    assert [(c.start_ms, c.end_ms) for c in chunks] == [(0, 4_100), (4_100, 8_000), (8_000, 10_000)]


def test_parse_silences_and_duration() -> None:
    stderr = (
        "  Duration: 00:01:02.50, start: 0.000000, bitrate: 64 kb/s\n"
        "[silencedetect @ 0x1] silence_start: 1.5\n"
        "[silencedetect @ 0x1] silence_end: 2.25 | silence_duration: 0.75\n"
        "[silencedetect @ 0x1] silence_start: -0.01\n"
        "[silencedetect @ 0x1] silence_end: 0.6 | silence_duration: 0.6\n"
    )
    assert audio.parse_duration_ms(stderr) == 62_500
    assert audio.parse_silences(stderr) == [(1500, 2250), (0, 600)]
    assert audio.parse_duration_ms("size=1 time=00:00:03.20 bitrate=1") == 3200
    assert audio.parse_duration_ms("nothing") is None


def is_empty_dir(path: Path) -> bool:
    return not any(path.iterdir())


async def make_tone_silence_tone(path: Path) -> None:
    """3 s tone, 2 s silence, 3 s tone (8 s total) as mp3."""
    code, err = await audio.run_ffmpeg(
        [
            "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono:d=2",
            "-f", "lavfi", "-i", "sine=frequency=660:duration=3",
            "-filter_complex", "[0:a][1:a][2:a]concat=n=3:v=0:a=1[out]",
            "-map", "[out]", "-ac", "1", "-c:a", "libmp3lame", "-b:a", "64k",
            "-loglevel", "error", "-y", str(path),
        ]
    )  # fmt: skip
    assert code == 0, err


async def test_real_ffmpeg_duration_silence_and_cut(tmp_path: Path) -> None:
    src = tmp_path / "src.mp3"
    await make_tone_silence_tone(src)
    duration = await audio.probe_duration_ms(src)
    assert 7_800 <= duration <= 8_300
    silences = await audio.detect_silences(src)
    assert len(silences) == 1
    start, end = silences[0]
    assert 2_800 <= start <= 3_300 and 4_700 <= end <= 5_200

    chunks = plan_chunks(duration, silences, chunk_ms=4_000, window_ms=1_500, min_last_ms=500)
    assert len(chunks) == 2
    assert 3_800 <= chunks[0].end_ms <= 4_200  # middle of the silence, not exactly 4 s

    out = tmp_path / "c0.mp3"
    data = await audio.cut_chunk(src, out, chunks[0].start_ms, chunks[0].end_ms)
    assert data[:3] == b"ID3" or data[0] == 0xFF  # mp3
    assert 3_700 <= await audio.probe_duration_ms(out) <= 4_400
    _, info = await audio.run_ffmpeg(["-i", str(out)])
    assert "16000 Hz" in info and "mono" in info


async def test_cut_failure_raises_clean_error(tmp_path: Path) -> None:
    bogus = tmp_path / "bogus.mp3"
    bogus.write_bytes(b"not audio at all")
    with pytest.raises(TranscriptionError):
        await audio.cut_chunk(bogus, tmp_path / "o.mp3", 0, 1000)
    with pytest.raises(TranscriptionError):
        await audio.probe_duration_ms(bogus)
