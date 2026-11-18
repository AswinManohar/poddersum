"""The transcript as prompt context, and the messages sent to the model.

Layout of every answer request (ask and presets alike)::

    system: <system prompt>
    user:   <transcript block>\\n\\n<task text>

The system prompt and the transcript block depend only on the episode, never on the
question or preset, so the long prefix of the request is byte-identical across requests
for the same episode. Gemini's implicit prompt caching then bills that prefix at the
cached-input rate on repeat requests; that is how a repeat question stays cheap.

Invariants:

* The context is text only (no audio or file parts).
* Every segment of the episode is included, in transcript order (no retrieval in M0).
* Rendering is deterministic: same segments in, same bytes out.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from agent.prompts import system_prompt

CHARS_PER_TOKEN = 4
"""Rough token estimate for English text; used only for the context-window guard."""


class SegmentLike(Protocol):
    """What we need from a ``db.models.Segment`` row."""

    id: int
    start_ms: int
    end_ms: int
    speaker: str | None
    text: str


@dataclass(frozen=True)
class SegmentRef:
    """A stored transcript segment, detached from the database session."""

    id: int
    start_ms: int
    end_ms: int
    speaker: str | None
    text: str

    @classmethod
    def from_row(cls, row: SegmentLike) -> SegmentRef:
        return cls(row.id, row.start_ms, row.end_ms, row.speaker, row.text)


class TranscriptTooLongError(Exception):
    """The prompt would not fit the model's context window."""


def format_timestamp(ms: int) -> str:
    """``MM:SS``, or ``H:MM:SS`` from one hour on."""
    seconds = max(0, ms) // 1000
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def render_segment(segment: SegmentRef) -> str:
    """One line: ``[<segment id>] [MM:SS] <Speaker>: <text>`` (speaker omitted if unknown)."""
    text = " ".join(segment.text.split())  # one line per segment, whatever the source had
    speaker = f"{segment.speaker}: " if segment.speaker else ""
    return f"[{segment.id}] [{format_timestamp(segment.start_ms)}] {speaker}{text}"


def render_transcript(segments: Sequence[SegmentRef]) -> str:
    """The whole transcript wrapped in ``<transcript>`` tags."""
    lines = "\n".join(render_segment(s) for s in segments)
    return f"<transcript>\n{lines}\n</transcript>"


def build_messages(segments: Sequence[SegmentRef], task_text: str) -> list[dict[str, Any]]:
    """System prompt, then one user message: transcript first, task last."""
    return [
        {"role": "system", "content": system_prompt().text},
        {"role": "user", "content": f"{render_transcript(segments)}\n\n{task_text}"},
    ]


def estimate_tokens(messages: Sequence[dict[str, Any]]) -> int:
    return sum(len(m["content"]) for m in messages) // CHARS_PER_TOKEN


def ensure_fits(
    messages: Sequence[dict[str, Any]], *, context_window: int | None, reserved_output: int
) -> None:
    """Raise :class:`TranscriptTooLongError` when the estimated prompt plus the tokens
    reserved for the reply exceed ``context_window`` (no check when it is unknown)."""
    if context_window is None:
        return
    needed = estimate_tokens(messages) + reserved_output
    if needed > context_window:
        raise TranscriptTooLongError(
            f"This episode's transcript is too long for the model (about {needed:,} tokens "
            f"needed, {context_window:,} available)."
        )
