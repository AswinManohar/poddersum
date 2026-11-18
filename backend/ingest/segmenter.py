"""Cues/turns -> citation-sized segments (pure functions, no I/O).

Rules:
* consecutive cues of the SAME speaker merge until a segment would exceed
  ``max_ms`` (~45 s) or ``max_words`` (~120 words); never merge across speakers;
* an over-long single cue/turn is split at sentence boundaries (falling back to word
  boundaries), with timestamps interpolated by character position;
* output is ordered by time, ``idx`` 0..n-1, ``end_ms >= start_ms``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from ingest.transcriber import Cue

MAX_SEGMENT_MS = 45_000
MAX_SEGMENT_WORDS = 120

_NO_SPACE_BEFORE = re.compile(r"^[,.;:!?%)\]}'’”]")
_SENTENCE_END = re.compile(r"(?<=[.!?…])[\"'’”)]*\s+")
_WS = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class SegmentData:
    idx: int
    start_ms: int
    end_ms: int
    speaker: str | None
    text: str


def join_text(parts: Iterable[str]) -> str:
    """Join cue texts with single spaces, without a space before closing punctuation."""
    out = ""
    for raw in parts:
        part = _WS.sub(" ", raw).strip()
        if not part:
            continue
        if not out:
            out = part
        elif _NO_SPACE_BEFORE.match(part) or out.endswith(("(", "[", "“")):
            out += part
        else:
            out += " " + part
    return out


def _words(text: str) -> int:
    return len(text.split())


def split_sentences(text: str) -> list[str]:
    return [s for s in _SENTENCE_END.split(text.strip()) if s]


def _pack(pieces: list[str], max_words: int) -> list[str]:
    """Greedy-pack sentences into chunks of at most ``max_words`` (a longer sentence is
    split on words)."""
    chunks: list[str] = []
    current: list[str] = []
    count = 0
    for piece in pieces:
        n = _words(piece)
        if n > max_words:
            if current:
                chunks.append(" ".join(current))
                current, count = [], 0
            words = piece.split()
            chunks.extend(" ".join(words[i : i + max_words]) for i in range(0, n, max_words))
            continue
        if current and count + n > max_words:
            chunks.append(" ".join(current))
            current, count = [], 0
        current.append(piece)
        count += n
    if current:
        chunks.append(" ".join(current))
    return chunks


def split_long(cue: Cue, *, max_ms: int, max_words: int) -> list[Cue]:
    """Split one over-long cue at sentence boundaries; interpolate timestamps by
    character offset."""
    duration = max(0, cue.end_ms - cue.start_ms)
    words = _words(cue.text)
    if words <= max_words and duration <= max_ms:
        return [cue]
    # Number of pieces needed for each limit; pack sentences toward the tighter one.
    pieces_needed = max(-(-words // max_words), -(-duration // max_ms) if max_ms else 1, 1)
    target_words = max(1, min(max_words, -(-words // pieces_needed)))
    chunks = _pack(split_sentences(cue.text), target_words)
    if len(chunks) <= 1:
        return [cue]
    total_chars = sum(len(c) for c in chunks) or 1
    out: list[Cue] = []
    pos = 0
    for i, chunk in enumerate(chunks):
        start = cue.start_ms + duration * pos // total_chars
        pos += len(chunk)
        end = cue.end_ms if i == len(chunks) - 1 else cue.start_ms + duration * pos // total_chars
        out.append(Cue(start, max(start, end), chunk, cue.speaker))
    return out


def segment_cues(
    cues: Iterable[Cue],
    *,
    max_ms: int = MAX_SEGMENT_MS,
    max_words: int = MAX_SEGMENT_WORDS,
) -> list[SegmentData]:
    ordered = sorted(
        (c for c in cues if c.text and c.text.strip()), key=lambda c: (c.start_ms, c.end_ms)
    )
    pieces: list[Cue] = []
    for c in ordered:
        pieces.extend(split_long(c, max_ms=max_ms, max_words=max_words))

    groups: list[list[Cue]] = []
    words = 0
    for c in pieces:
        if groups:
            g = groups[-1]
            same = g[0].speaker == c.speaker
            n = _words(c.text)
            span = max(c.end_ms, g[-1].end_ms) - g[0].start_ms
            if same and words + n <= max_words and span <= max_ms:
                g.append(c)
                words += n
                continue
        groups.append([c])
        words = _words(c.text)

    out: list[SegmentData] = []
    for g in groups:
        start = max(g[0].start_ms, 0)
        end = max(max(c.end_ms for c in g), start)
        text = join_text(c.text for c in g)
        if not text:
            continue
        out.append(SegmentData(len(out), start, end, g[0].speaker, text))
    return out
