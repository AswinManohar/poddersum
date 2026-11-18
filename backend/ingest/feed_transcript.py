"""Source 1: the feed's own ``<podcast:transcript>`` file (cost 0, source ``feed``).

Only formats with timestamps are used, in this order of preference:
VTT (``text/vtt``) > SRT (``application/x-subrip``, ``application/srt``, ``text/srt``) >
Podcasting 2.0 JSON (``application/json``: ``{"segments": [{startTime, endTime, speaker,
body}]}``). HTML and plain text have no timestamps and are ignored. When the type is
missing, the URL extension decides.

Speakers come from VTT voice tags (``<v Name>``) or a short ``Name:`` prefix (SRT files
usually label only the first cue of a turn, so a label carries forward until the next one).
Any fetch/parse failure, or a file without timed cues, returns ``None`` so the runner falls
through to audio transcription.
"""

from __future__ import annotations

import html
import json
import logging
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from ingest.transcriber import Cue, TranscriptResult

log = logging.getLogger(__name__)

MAX_TRANSCRIPT_BYTES = 20 * 1024 * 1024
FETCH_TIMEOUT = httpx.Timeout(20.0, connect=5.0)

VTT_TYPES = frozenset({"text/vtt"})
SRT_TYPES = frozenset({"application/x-subrip", "application/srt", "text/srt"})
JSON_TYPES = frozenset({"application/json"})
_EXTENSION_FORMAT = {".vtt": "vtt", ".srt": "srt", ".json": "json"}
FORMAT_PREFERENCE = ("vtt", "srt", "json")


@dataclass(frozen=True, slots=True)
class TagCandidate:
    url: str
    format: str  # "vtt" | "srt" | "json"
    type: str | None
    language: str | None


def tag_format(tag: Mapping[str, Any]) -> str | None:
    """``vtt``/``srt``/``json`` for a usable tag, ``None`` for html/plain/unknown."""
    mime = (tag.get("type") or "").split(";")[0].strip().lower()
    if mime in VTT_TYPES:
        return "vtt"
    if mime in SRT_TYPES:
        return "srt"
    if mime in JSON_TYPES:
        return "json"
    if mime:
        return None
    path = str(tag.get("url") or "").split("?")[0].split("#")[0].lower()
    for ext, fmt in _EXTENSION_FORMAT.items():
        if path.endswith(ext):
            return fmt
    return None


def rank_tags(tags: Iterable[Mapping[str, Any]]) -> list[TagCandidate]:
    """Usable tags, best first (format preference, then feed order)."""
    found: list[tuple[int, int, TagCandidate]] = []
    for order, tag in enumerate(tags):
        fmt = tag_format(tag)
        url = tag.get("url")
        if fmt is None or not url:
            continue
        cand = TagCandidate(url=url, format=fmt, type=tag.get("type"), language=tag.get("language"))
        found.append((FORMAT_PREFERENCE.index(fmt), order, cand))
    return [c for _, _, c in sorted(found, key=lambda t: (t[0], t[1]))]


# --------------------------------------------------------------------------- parsing

_TIMING = re.compile(
    r"^\s*(?P<start>\d{1,3}(?::\d{1,2}){1,2}(?:[.,]\d{1,3})?)\s*-->\s*"
    r"(?P<end>\d{1,3}(?::\d{1,2}){1,2}(?:[.,]\d{1,3})?)"
)
_VOICE = re.compile(r"<v(?:\.[^\s>]*)?\s+([^>]+)>", re.I)
_TAG = re.compile(r"</?[^>]+>")
_INLINE_TS = re.compile(r"<\d{1,2}(?::\d{2}){1,2}[.,]\d{3}>")
# "Name: text" where Name is 1-4 capitalised-ish words without digits, at most 40 chars.
_PREFIX = re.compile(r"^(?P<name>[A-Z][\w.'\-]*(?:\s+[A-Z&][\w.'\-]*){0,3}):\s+(?P<rest>\S.*)$")
_WS = re.compile(r"\s+")


def parse_timestamp(value: str) -> int:
    """``HH:MM:SS.mmm``, ``MM:SS.mmm`` or with a comma -> milliseconds."""
    value = value.strip().replace(",", ".")
    main, _, frac = value.partition(".")
    parts = [int(p) for p in main.split(":")]
    seconds = 0
    for p in parts:
        seconds = seconds * 60 + p
    ms = int((frac + "000")[:3]) if frac else 0
    return seconds * 1000 + ms


def _clean(text: str) -> str:
    text = _INLINE_TS.sub("", text)
    text = _TAG.sub("", text)
    return _WS.sub(" ", html.unescape(text)).strip()


def _split_speaker(text: str) -> tuple[str | None, str]:
    m = _PREFIX.match(text)
    if m and len(m.group("name")) <= 40 and not any(ch.isdigit() for ch in m.group("name")):
        return m.group("name").strip(), m.group("rest").strip()
    return None, text


def _carry_speakers(cues: list[Cue]) -> list[Cue]:
    """A speaker label applies until the next one (common in SRT/VTT exports)."""
    out: list[Cue] = []
    current: str | None = None
    for c in cues:
        if c.speaker:
            current = c.speaker
            out.append(c)
        else:
            out.append(Cue(c.start_ms, c.end_ms, c.text, current))
    return out


def _blocks(text: str) -> list[list[str]]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return [b.split("\n") for b in re.split(r"\n\s*\n", text) if b.strip()]


def _timed_cues(blocks: Sequence[Sequence[str]], *, voice_tags: bool) -> list[Cue]:
    cues: list[Cue] = []
    for lines in blocks:
        timing_at = next((i for i, ln in enumerate(lines) if _TIMING.match(ln)), None)
        if timing_at is None:
            continue
        m = _TIMING.match(lines[timing_at])
        assert m is not None
        start, end = parse_timestamp(m.group("start")), parse_timestamp(m.group("end"))
        body = "\n".join(lines[timing_at + 1 :])
        speaker: str | None = None
        if voice_tags:
            vm = _VOICE.search(body)
            if vm:
                speaker = _WS.sub(" ", vm.group(1)).strip() or None
        text = _clean(body)
        if speaker is None:
            speaker, text = _split_speaker(text)
        if not text:
            continue
        cues.append(Cue(start, max(start, end), text, speaker))
    return _carry_speakers(cues)


def parse_vtt(text: str) -> list[Cue]:
    text = text.lstrip("﻿")
    if not text.lstrip().startswith("WEBVTT"):
        raise ValueError("not a WebVTT file")
    blocks = [
        b
        for b in _blocks(text)[1:]  # header block
        if not b[0].startswith(("NOTE", "STYLE", "REGION"))
    ]
    return _timed_cues(blocks, voice_tags=True)


def parse_srt(text: str) -> list[Cue]:
    return _timed_cues(_blocks(text.lstrip("﻿")), voice_tags=False)


def _seconds_ms(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return int(round(float(value) * 1000))
    if isinstance(value, str) and value.strip():
        s = value.strip()
        if ":" in s:
            return parse_timestamp(s)
        return int(round(float(s) * 1000))
    return None


def parse_json_transcript(data: Any) -> list[Cue]:
    """Podcasting 2.0 JSON. Word-level files (Buzzsprout) are fine: the segmenter merges."""
    if isinstance(data, str | bytes):
        data = json.loads(data)
    segments = data.get("segments") if isinstance(data, dict) else None
    if not isinstance(segments, list):
        raise ValueError("JSON transcript has no segments list")
    rows: list[tuple[int, int | None, str, str | None]] = []
    for seg in segments:
        if not isinstance(seg, dict):
            continue
        start = _seconds_ms(seg.get("startTime"))
        body = _clean(str(seg.get("body") or ""))
        if start is None or not body:
            continue
        raw_speaker = seg.get("speaker")
        speaker = _WS.sub(" ", raw_speaker).strip() if isinstance(raw_speaker, str) else ""
        rows.append((start, _seconds_ms(seg.get("endTime")), body, speaker or None))
    cues: list[Cue] = []
    for i, (start, end, body, speaker) in enumerate(rows):
        if end is None or end < start:
            end = rows[i + 1][0] if i + 1 < len(rows) else start
        cues.append(Cue(start, max(start, end), body, speaker))
    return _carry_speakers(cues)


def parse_transcript(content: bytes, fmt: str) -> list[Cue]:
    if fmt == "json":
        return parse_json_transcript(content)
    text = _decode(content)
    if fmt == "vtt":
        return parse_vtt(text)
    if fmt == "srt":
        return parse_srt(text)
    raise ValueError(f"unsupported transcript format {fmt!r}")


def _decode(content: bytes) -> str:
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return content.decode("latin-1")


# --------------------------------------------------------------------------- fetching


async def fetch_bytes(
    http: httpx.AsyncClient, url: str, *, max_bytes: int = MAX_TRANSCRIPT_BYTES
) -> bytes:
    async with http.stream("GET", url, follow_redirects=True, timeout=FETCH_TIMEOUT) as resp:
        resp.raise_for_status()
        declared = resp.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > max_bytes:
            raise ValueError(f"transcript larger than {max_bytes} bytes")
        buf = bytearray()
        async for chunk in resp.aiter_bytes():
            buf.extend(chunk)
            if len(buf) > max_bytes:
                raise ValueError(f"transcript larger than {max_bytes} bytes")
        return bytes(buf)


async def fetch_feed_transcript(
    http: httpx.AsyncClient, tags: Iterable[Mapping[str, Any]]
) -> TranscriptResult | None:
    """Best usable feed transcript, or ``None`` (caller falls through to audio STT)."""
    for cand in rank_tags(tags):
        try:
            content = await fetch_bytes(http, cand.url)
            cues = [c for c in parse_transcript(content, cand.format) if c.text]
        except Exception as exc:  # noqa: BLE001 - any failure means "try the next source"
            log.info("feed transcript %s unusable: %s", cand.url, type(exc).__name__)
            continue
        if not cues:
            log.info("feed transcript %s has no timed cues", cand.url)
            continue
        cues.sort(key=lambda c: c.start_ms)
        return TranscriptResult(
            source="feed",
            cues=cues,
            source_url=cand.url,
            language=cand.language,
            duration_ms=max(c.end_ms for c in cues),
            raw={
                "format": cand.format,
                "tag": {"url": cand.url, "type": cand.type, "language": cand.language},
                "cues": [c.as_json() for c in cues],
            },
        )
    return None
