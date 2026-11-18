"""transcripts, segments, jobs, job_events

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-12

* ``transcripts``: one per episode (unique ``episode_id``); source ``feed`` | ``gemini``,
  vendor model and cost, the parsed vendor/feed payload in ``raw``.
* ``segments``: citation-sized, ordered, timestamped text. ``id`` is the bigint that
  ``Citation.segment_id`` points at. ``tsv`` is a generated english tsvector (GIN).
* ``jobs`` + ``job_events``: ingest jobs and their persisted, replayable SSE events.
  ``uq_jobs_active_ingest`` allows at most one queued/running ingest job per episode.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _now(name: str, nullable: bool = False) -> sa.Column:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=nullable,
        server_default=None if nullable else sa.func.now(),
    )


def upgrade() -> None:
    op.create_table(
        "transcripts",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), primary_key=True),
        sa.Column(
            "episode_id",
            sa.Text(),
            sa.ForeignKey("episodes.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=False, server_default="0"),
        sa.Column("tokens_in", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_out", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("language", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("raw", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("storage_key", sa.Text(), nullable=True),
        _now("created_at"),
        sa.CheckConstraint("source IN ('feed', 'gemini')", name="ck_transcripts_source"),
    )

    op.create_table(
        "segments",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), primary_key=True),
        sa.Column(
            "transcript_id",
            sa.BigInteger(),
            sa.ForeignKey("transcripts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "episode_id",
            sa.Text(),
            sa.ForeignKey("episodes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("idx", sa.Integer(), nullable=False),
        sa.Column("start_ms", sa.Integer(), nullable=False),
        sa.Column("end_ms", sa.Integer(), nullable=False),
        sa.Column("speaker", sa.Text(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "tsv",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('english'::regconfig, text)", persisted=True),
        ),
        sa.UniqueConstraint("episode_id", "idx", name="uq_segments_episode_idx"),
        sa.CheckConstraint("end_ms >= start_ms AND start_ms >= 0", name="ck_segments_times"),
    )
    op.create_index("ix_segments_episode_id", "segments", ["episode_id"])
    op.create_index("ix_segments_transcript_id", "segments", ["transcript_id"])
    op.create_index("ix_segments_tsv", "segments", ["tsv"], postgresql_using="gin")

    op.create_table(
        "jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "episode_id",
            sa.Text(),
            sa.ForeignKey("episodes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.Text(), nullable=False, server_default="ingest"),
        sa.Column("state", sa.Text(), nullable=False, server_default="queued"),
        sa.Column("step", sa.Text(), nullable=False, server_default="queued"),
        sa.Column("progress", sa.Float(), nullable=False, server_default="0"),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("last_seq", sa.Integer(), nullable=False, server_default="0"),
        _now("created_at"),
        _now("updated_at"),
        _now("finished_at", nullable=True),
        sa.CheckConstraint(
            "state IN ('queued', 'running', 'succeeded', 'failed')", name="ck_jobs_state"
        ),
        sa.CheckConstraint("kind IN ('ingest')", name="ck_jobs_kind"),
    )
    op.create_index("ix_jobs_episode_created", "jobs", ["episode_id", sa.text("created_at DESC")])
    op.create_index(
        "uq_jobs_active_ingest",
        "jobs",
        ["episode_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'ingest' AND state IN ('queued', 'running')"),
    )

    op.create_table(
        "job_events",
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("seq", sa.Integer(), primary_key=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        _now("ts"),
    )


def downgrade() -> None:
    op.drop_table("job_events")
    op.drop_index("uq_jobs_active_ingest", table_name="jobs")
    op.drop_index("ix_jobs_episode_created", table_name="jobs")
    op.drop_table("jobs")
    op.drop_index("ix_segments_tsv", table_name="segments")
    op.drop_index("ix_segments_transcript_id", table_name="segments")
    op.drop_index("ix_segments_episode_id", table_name="segments")
    op.drop_table("segments")
    op.drop_table("transcripts")
