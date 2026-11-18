"""SQLAlchemy 2 ORM models. Migration 0001: ``podcasts``, ``episodes``; 0002:
``transcripts``, ``segments``, ``jobs``, ``job_events``; 0003: ``artifacts``, ``requests``.

Keep these models in sync with ``db/migrations/versions``. Row classes that share a name
with a contract model in ``api.schemas`` get a ``Row`` suffix (``JobEventRow``,
``ArtifactRow``, ``RequestRow``).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class Podcast(TimestampMixin, Base):
    __tablename__ = "podcasts"

    # sha256(normalize_feed_url(feed_url)), see discovery/ids.py
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    # The feed URL as recorded from iTunes (fetchable); the id hashes its normalized form.
    feed_url: Mapped[str] = mapped_column(Text, nullable=False)
    itunes_id: Mapped[int | None] = mapped_column(BigInteger)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    author: Mapped[str | None] = mapped_column(Text)
    artwork_url: Mapped[str | None] = mapped_column(Text)
    genre: Mapped[str | None] = mapped_column(Text)

    episodes: Mapped[list[Episode]] = relationship(back_populates="podcast", lazy="raise")


class Episode(TimestampMixin, Base):
    __tablename__ = "episodes"
    __table_args__ = (
        Index(
            "ix_episodes_podcast_published",
            "podcast_id",
            text("published_at DESC NULLS LAST"),
        ),
    )

    # sha256(normalize_feed_url(feed_url) + guid), see discovery/ids.py
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    podcast_id: Mapped[str] = mapped_column(
        Text, ForeignKey("podcasts.id", ondelete="CASCADE"), nullable=False
    )
    # Resolved identity string (guid, else enclosure URL, else "title|published").
    guid: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_s: Mapped[int | None] = mapped_column(Integer)
    audio_url: Mapped[str] = mapped_column(Text, nullable=False)
    audio_type: Mapped[str | None] = mapped_column(Text)
    artwork_url: Mapped[str | None] = mapped_column(Text)
    # List of {"url", "type", "language", "rel"} from <podcast:transcript>, in feed order.
    transcript_tags: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )

    podcast: Mapped[Podcast] = relationship(back_populates="episodes", lazy="raise")


class Transcript(Base):
    """One stored transcript per episode (process once, serve many)."""

    __tablename__ = "transcripts"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    episode_id: Mapped[str] = mapped_column(
        Text, ForeignKey("episodes.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    source: Mapped[str] = mapped_column(Text, nullable=False)  # "feed" | "gemini"
    source_url: Mapped[str | None] = mapped_column(Text)  # transcript file or audio URL
    model: Mapped[str | None] = mapped_column(Text)  # vendor model id (from LLMResult.model)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, server_default="0")
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    language: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    raw: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    storage_key: Mapped[str | None] = mapped_column(Text)  # object storage key (Phase 1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Segment(Base):
    """A citation-sized piece of a transcript. ``id`` is ``Citation.segment_id``."""

    __tablename__ = "segments"
    __table_args__ = (
        UniqueConstraint("episode_id", "idx", name="uq_segments_episode_idx"),
        Index("ix_segments_tsv", "tsv", postgresql_using="gin"),  # full-text search
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    transcript_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("transcripts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    episode_id: Mapped[str] = mapped_column(
        Text, ForeignKey("episodes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    idx: Mapped[int] = mapped_column(Integer, nullable=False)
    start_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    end_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    speaker: Mapped[str | None] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    tsv: Mapped[Any] = mapped_column(
        TSVECTOR, Computed("to_tsvector('english'::regconfig, text)", persisted=True)
    )


ACTIVE_JOB_STATES = ("queued", "running")


class Job(Base):
    """An ingest job. At most one queued/running ingest job per episode
    (partial unique index ``uq_jobs_active_ingest``)."""

    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_episode_created", "episode_id", text("created_at DESC")),
        Index(
            "uq_jobs_active_ingest",
            "episode_id",
            unique=True,
            postgresql_where=text("kind = 'ingest' AND state IN ('queued', 'running')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    episode_id: Mapped[str] = mapped_column(
        Text, ForeignKey("episodes.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False, server_default="ingest")
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default="queued")
    step: Mapped[str] = mapped_column(Text, nullable=False, server_default="queued")
    progress: Mapped[float] = mapped_column(Float, nullable=False, server_default="0")
    message: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    # seq of the last event in job_events (events are numbered 1, 2, ... per job)
    last_seq: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class JobEventRow(Base):
    """A persisted job progress event; ``payload`` is the ``JobEvent`` JSON."""

    __tablename__ = "job_events"

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True
    )
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


ARTIFACT_KINDS = ("tldr", "takeaways", "verdict")  # == api.schemas.ArtifactKind values
REQUEST_FEATURES = ("ask", "preset", "chat")


class ArtifactRow(Base):
    """A cached preset answer; ``answer`` is the serialized contract ``Answer``.
    Cache key: (``episode_id``, ``kind``, ``prompt_version``)."""

    __tablename__ = "artifacts"
    __table_args__ = (
        UniqueConstraint(
            "episode_id", "kind", "prompt_version", name="uq_artifacts_episode_kind_version"
        ),
        CheckConstraint("kind IN ('tldr', 'takeaways', 'verdict')", name="ck_artifacts_kind"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    episode_id: Mapped[str] = mapped_column(
        Text, ForeignKey("episodes.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_version: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RequestRow(Base):
    """One ask / preset / chat call with its answer, tokens, cost and latency.
    ``cached`` = served from ``artifacts`` with zero model calls."""

    __tablename__ = "requests"
    __table_args__ = (
        Index("ix_requests_episode_created", "episode_id", text("created_at DESC")),
        CheckConstraint("feature IN ('ask', 'preset', 'chat')", name="ck_requests_feature"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[str | None] = mapped_column(Text)  # no auth in M0
    episode_id: Mapped[str] = mapped_column(
        Text, ForeignKey("episodes.id", ondelete="CASCADE"), nullable=False
    )
    feature: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str | None] = mapped_column(Text)  # preset kind
    question: Mapped[str | None] = mapped_column(Text)
    answer: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    prompt_version: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str | None] = mapped_column(Text)
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    tokens_cached: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, server_default="0")
    cached: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
