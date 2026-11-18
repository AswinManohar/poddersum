"""podcasts and episodes

Revision ID: 0001
Revises:
Create Date: 2026-11-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "podcasts",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("feed_url", sa.Text(), nullable=False),
        sa.Column("itunes_id", sa.BigInteger(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("author", sa.Text(), nullable=True),
        sa.Column("artwork_url", sa.Text(), nullable=True),
        sa.Column("genre", sa.Text(), nullable=True),
        *_timestamps(),
    )
    op.create_table(
        "episodes",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "podcast_id",
            sa.Text(),
            sa.ForeignKey("podcasts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("guid", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_s", sa.Integer(), nullable=True),
        sa.Column("audio_url", sa.Text(), nullable=False),
        sa.Column("audio_type", sa.Text(), nullable=True),
        sa.Column("artwork_url", sa.Text(), nullable=True),
        sa.Column(
            "transcript_tags",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        *_timestamps(),
    )
    op.create_index(
        "ix_episodes_podcast_published",
        "episodes",
        ["podcast_id", sa.text("published_at DESC NULLS LAST")],
    )


def downgrade() -> None:
    op.drop_index("ix_episodes_podcast_published", table_name="episodes")
    op.drop_table("episodes")
    op.drop_table("podcasts")
