"""Transcript data types and the speech-to-text ``Transcriber`` interface.

Every transcript source (a feed ``<podcast:transcript>`` file, or an STT vendor) produces a
:class:`TranscriptResult`: timed :class:`Cue` objects plus accounting. STT vendors sit
behind :class:`Transcriber` so the runner never knows which one it is talking to; the
vendor, model and cost end up on the ``transcripts`` row.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from api.schemas import JobStep


class TranscriptionError(RuntimeError):
    """A transcript could not be produced. The message is short, user-safe and secret-free."""


@dataclass(frozen=True, slots=True)
class Cue:
    """A timed piece of transcript text (ms from the start of the audio)."""

    start_ms: int
    end_ms: int
    text: str
    speaker: str | None = None

    def as_json(self) -> dict[str, Any]:
        return {
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "speaker": self.speaker,
            "text": self.text,
        }


@dataclass(slots=True)
class TranscriptResult:
    """What a source hands to the segmenter and the ``transcripts`` row."""

    source: str  # "feed" | "gemini" (transcripts.source)
    cues: list[Cue]
    source_url: str | None = None
    model: str | None = None
    cost_usd: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    language: str | None = None
    duration_ms: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)


Reporter = Callable[[JobStep, float, str | None], Awaitable[None]]
"""Progress callback: ``(step, fraction_of_this_source_0_to_1, message)``."""


async def no_report(_step: JobStep, _fraction: float, _message: str | None) -> None:
    return None


@dataclass(frozen=True, slots=True)
class AudioSource:
    """The audio a transcriber works from."""

    episode_id: str
    audio_url: str
    title: str | None = None
    podcast_title: str | None = None
    duration_s: int | None = None  # from the feed; informational only


class Transcriber(Protocol):
    """A speech-to-text vendor. Implementations own their temp files and delete them."""

    source: str  # value for transcripts.source

    async def transcribe(
        self, audio: AudioSource, report: Reporter = no_report
    ) -> TranscriptResult:
        """Transcribe ``audio`` (download included). Reports ``download_audio`` and
        ``transcribe`` progress. Raises :class:`TranscriptionError` or ``llm.LLMError``."""
        ...
