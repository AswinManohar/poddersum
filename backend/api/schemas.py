"""PodderSum prototype API contract v0 (frozen at the end of M0-S1).

Every request and response body of the prototype lives here and is mirrored to
``frontend/lib/types.ts`` (generated from OpenAPI by ``scripts/export_openapi.py``).
Changes after the freeze must be flagged in the slice report and regenerate types.ts.

Conventions
-----------
* Ids are strings. ``podcast_id`` and ``episode_id`` are 64-char lowercase hex SHA-256
  digests (see ``discovery/ids.py``). ``job_id`` and chat message ids are UUID strings.
* Times are ISO-8601 with a timezone (UTC). Media offsets are integer milliseconds.
* Errors use :class:`ErrorResponse` (``{"detail": ..., "code": ...}``). Request validation
  failures keep FastAPI's default 422 body.

Routes (v0)
-----------
=======  ===================================  ===========================================
Method   Path                                 Body
=======  ===================================  ===========================================
GET      /search?q=&limit=                    -> SearchResponse
GET      /podcasts/{podcast_id}/episodes      -> PodcastEpisodes (``limit``, default 50)
GET      /episodes/{episode_id}               -> EpisodeDetail
POST     /episodes/{episode_id}/ingest        -> 202 JobStatus
GET      /jobs/{job_id}                       -> JobStatus
GET      /jobs/{job_id}/events                -> SSE stream of JobEvent
POST     /episodes/{episode_id}/ask           AskRequest -> SSE stream of AnswerStreamEvent
GET      /episodes/{episode_id}/artifacts/{kind}  -> Artifact
POST     /episodes/{episode_id}/chat          ChatRequest -> SSE stream of AnswerStreamEvent
GET      /episodes/{episode_id}/chat          -> list[ChatMessage]
=======  ===================================  ===========================================

Server-sent events
------------------
Job progress (``GET /jobs/{job_id}/events``): every frame is ``event: job``, ``id: <seq>``,
``data: <JobEvent JSON>``. The stream replays from ``Last-Event-ID`` and closes after the
event whose ``state`` is ``succeeded`` or ``failed``.

Answers (``POST /episodes/{id}/ask`` and ``POST /episodes/{id}/chat``): the SSE ``event:``
name equals the ``event`` field of the JSON ``data`` (an :data:`AnswerStreamEvent`):

* ``status`` - :class:`StatusEvent` ``{message}``: human-readable progress.
* ``claim``  - :class:`ClaimEvent` ``{index, claim}``: one validated, cited claim, emitted
  as soon as it is ready, in final order.
* ``answer`` - :class:`AnswerEvent` ``{answer}``: the complete :class:`Answer` (all claims
  again plus model, tokens and cost). Clients should replace streamed claims with it.
* ``error``  - :class:`ErrorEvent` ``{message, code}``: the request failed; ``done`` follows.
* ``done``   - :class:`DoneEvent` ``{}``: always the last frame.

Order: ``status*  claim*  (answer | error)  done``. These streams are not replayable.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

# --- shared -----------------------------------------------------------------

Sha256Id = Annotated[
    str,
    Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
        description="64-char lowercase hex SHA-256 id.",
    ),
]


class Model(BaseModel):
    # Response fields that have defaults are still always sent, so mark them required in the
    # serialization (response) JSON schema; generated TS types then have no spurious `?`.
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)


class ErrorResponse(Model):
    """Body of every non-2xx response except request validation errors (422)."""

    detail: str = Field(description="Human-readable message, safe to show to users.")
    code: str = Field(
        description="Stable machine-readable code, e.g. `not_found`, `upstream_error`, "
        "`not_implemented`, `conflict`."
    )


# --- enums ------------------------------------------------------------------


class EpisodeStatus(StrEnum):
    """Processing status of an episode's transcript.

    * ``new`` - never ingested (or no job yet): call ``POST /episodes/{id}/ingest``.
    * ``processing`` - an ingest job is queued or running (see ``active_job_id``).
    * ``ready`` - a transcript with segments is stored; ask, presets and chat work.
    * ``failed`` - the last ingest job failed; ingest may be retried.
    """

    new = "new"
    processing = "processing"
    ready = "ready"
    failed = "failed"


class JobState(StrEnum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"


class JobStep(StrEnum):
    """Ingest pipeline steps, in order. A feed-transcript episode skips ``download_audio``
    and ``transcribe``. ``done`` is reported once ``state`` is ``succeeded``."""

    queued = "queued"
    fetch_transcript = "fetch_transcript"
    download_audio = "download_audio"
    transcribe = "transcribe"
    segment = "segment"
    store = "store"
    done = "done"


class ArtifactKind(StrEnum):
    """Cached preset answers. ``verdict`` = "is this worth my time?"."""

    tldr = "tldr"
    takeaways = "takeaways"
    verdict = "verdict"


class VerdictSection(StrEnum):
    """Known values of ``Claim.section`` for ``verdict`` artifacts, in display order."""

    who_its_for = "who_its_for"
    whats_new = "whats_new"
    best_10_minutes = "best_10_minutes"


class ChatRole(StrEnum):
    user = "user"
    assistant = "assistant"


# --- discovery --------------------------------------------------------------


class PodcastHit(Model):
    """A podcast (show), from search or from the database."""

    id: Sha256Id = Field(description="sha256(normalized feed URL).")
    title: str
    author: str | None = None
    artwork_url: str | None = Field(default=None, description="Square artwork, up to 600px.")
    feed_url: str = Field(description="RSS feed URL as listed by iTunes.")
    genre: str | None = Field(default=None, description="Primary genre name.")
    itunes_id: int | None = Field(default=None, description="Apple Podcasts collection id.")


class SearchResponse(Model):
    """Result of ``GET /search``. Only shows with a public RSS feed are returned."""

    query: str
    results: list[PodcastHit]


class TranscriptTag(Model):
    """A ``<podcast:transcript>`` tag from the feed. Ingest prefers VTT, then SRT, then JSON."""

    url: str
    type: str | None = Field(
        default=None, description="MIME type, e.g. `text/vtt`, `application/x-subrip`."
    )
    language: str | None = None
    rel: str | None = Field(default=None, description="`captions` when the file is captions.")


class EpisodeRef(Model):
    """An episode as shown in a list."""

    id: Sha256Id = Field(description="sha256(normalized feed URL + guid).")
    podcast_id: Sha256Id
    title: str
    published_at: datetime | None = None
    duration_s: int | None = Field(default=None, ge=0, description="Duration in seconds.")
    description: str | None = Field(
        default=None, description="Plain-text show notes, at most 2000 characters."
    )
    artwork_url: str | None = Field(
        default=None, description="Episode artwork, falling back to the podcast's."
    )
    has_feed_transcript: bool = Field(
        description="True when the feed publishes a transcript (no audio transcription needed)."
    )
    status: EpisodeStatus = Field(
        default=EpisodeStatus.new, description="Transcript processing status."
    )


class PodcastEpisodes(Model):
    """Result of ``GET /podcasts/{podcast_id}/episodes``: newest first."""

    podcast: PodcastHit
    episodes: list[EpisodeRef]
    total: int = Field(ge=0, description="Episodes with audio in the feed (before `limit`).")


class EpisodeDetail(EpisodeRef):
    """Result of ``GET /episodes/{episode_id}``."""

    podcast: PodcastHit
    guid: str = Field(description="Feed guid (or fallback identity) the id was derived from.")
    audio_url: str = Field(description="Original enclosure URL; citation links use it.")
    audio_type: str | None = Field(default=None, description="Enclosure MIME type.")
    transcript_tags: list[TranscriptTag] = Field(default_factory=list)
    active_job_id: str | None = Field(
        default=None, description="Queued or running ingest job, when `status` is `processing`."
    )


# --- jobs -------------------------------------------------------------------


class JobStatus(Model):
    """State of an ingest job. ``POST /episodes/{id}/ingest`` is idempotent: when a job for
    the episode is already queued or running (or the episode is ready) it returns that job."""

    job_id: str = Field(description="UUID string.")
    episode_id: Sha256Id
    state: JobState
    step: JobStep
    progress: float = Field(ge=0.0, le=1.0, description="Overall progress, 0..1.")
    message: str | None = Field(default=None, description="Human-readable step detail.")
    error: str | None = Field(default=None, description="Failure reason when `state=failed`.")
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None = None


class JobEvent(Model):
    """One SSE frame of ``GET /jobs/{job_id}/events`` (``event: job``, ``id: <seq>``).

    Events are persisted and replayable: ``seq`` starts at 1 and increases by 1 per job.
    A client that reconnects with the ``Last-Event-ID: <seq>`` header (browsers'
    ``EventSource`` do this automatically) receives every event with a greater ``seq``,
    then live events. The stream closes after a ``succeeded`` or ``failed`` event.
    """

    seq: int = Field(ge=1, description="Per-job sequence number; also the SSE `id`.")
    job_id: str
    state: JobState
    step: JobStep
    progress: float = Field(ge=0.0, le=1.0)
    message: str | None = None
    ts: datetime = Field(description="When the event happened.")


# --- answers ----------------------------------------------------------------


class Citation(Model):
    """A pointer into the stored transcript. ``segment_id`` is the integer primary key of
    the ``segments`` row; ``start_ms`` is the playback offset for the audio link."""

    segment_id: int = Field(ge=1, description="`segments.id` (bigint).")
    start_ms: int = Field(ge=0, description="Segment start, ms from the start of the audio.")
    end_ms: int | None = Field(default=None, ge=0)
    speaker: str | None = None
    quote: str | None = Field(default=None, description="Short supporting excerpt.")


class Claim(Model):
    """One statement of an answer. Uncited claims are dropped server-side, never returned."""

    text: str = Field(min_length=1)
    citations: list[Citation] = Field(min_length=1)
    section: str | None = Field(
        default=None,
        description="Optional grouping label. For `verdict` artifacts one of "
        "`who_its_for`, `whats_new`, `best_10_minutes` (see VerdictSection).",
    )


class Answer(Model):
    """A grounded answer: ordered claims plus accounting for the model call."""

    claims: list[Claim]
    model: str = Field(description="Model id actually used (from llm/models.yaml).")
    prompt_version: str
    tokens_in: int = Field(ge=0)
    tokens_out: int = Field(ge=0)
    cost_usd: float = Field(ge=0.0)
    cached: bool = Field(description="True when served from cache with no model call.")


class AskRequest(Model):
    question: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
    ]


class Artifact(Model):
    """A cached preset answer for an episode, keyed by (episode, kind, prompt_version)."""

    episode_id: Sha256Id
    kind: ArtifactKind
    prompt_version: str
    answer: Answer
    created_at: datetime


# --- chat -------------------------------------------------------------------


class ChatRequest(Model):
    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class ChatMessage(Model):
    """A turn of the episode chat, as returned by ``GET /episodes/{id}/chat`` (oldest first)."""

    id: str = Field(description="UUID string.")
    role: ChatRole
    text: str = Field(description="User text, or the assistant's claims joined as prose.")
    claims: list[Claim] = Field(
        default_factory=list, description="Cited claims; always empty for `user` messages."
    )
    created_at: datetime


# --- answer stream events (ask and chat SSE) --------------------------------


class StatusEvent(Model):
    event: Literal["status"] = "status"
    message: str


class ClaimEvent(Model):
    event: Literal["claim"] = "claim"
    index: int = Field(ge=0, description="0-based position of the claim in the answer.")
    claim: Claim


class AnswerEvent(Model):
    event: Literal["answer"] = "answer"
    answer: Answer


class ErrorEvent(Model):
    event: Literal["error"] = "error"
    message: str
    code: str = "error"


class DoneEvent(Model):
    event: Literal["done"] = "done"


AnswerStreamEvent = Annotated[
    StatusEvent | ClaimEvent | AnswerEvent | ErrorEvent | DoneEvent,
    Field(discriminator="event"),
]
"""Data of one ask/chat SSE frame; ``event`` equals the SSE ``event:`` name."""

ANSWER_STREAM_EVENT_NAMES = ("status", "claim", "answer", "error", "done")
JOB_STREAM_EVENT_NAME = "job"

CONTRACT_MODELS: tuple[type[BaseModel], ...] = (
    ErrorResponse,
    PodcastHit,
    SearchResponse,
    TranscriptTag,
    EpisodeRef,
    PodcastEpisodes,
    EpisodeDetail,
    JobStatus,
    JobEvent,
    Citation,
    Claim,
    Answer,
    AskRequest,
    Artifact,
    ChatRequest,
    ChatMessage,
    StatusEvent,
    ClaimEvent,
    AnswerEvent,
    ErrorEvent,
    DoneEvent,
)
"""Every model that must appear in the OpenAPI components (checked by tests)."""
