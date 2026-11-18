"""artifacts, requests

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-13

* ``artifacts``: cached preset answers. ``answer`` is the serialized contract ``Answer``.
  The cache key is UNIQUE (``episode_id``, ``kind``, ``prompt_version``); bumping a
  prompt version is a cache miss. ``kind`` is text + a check constraint (not an enum) so
  Phase 1 can add kinds by replacing ``ck_artifacts_kind`` in a small migration.
* ``requests``: one row per ask / preset / chat call, for cost and usage accounting.
  ``user_id`` is nullable (no auth in M0). ``cached`` = served from the artifact cache
  with zero model calls. ``answer`` (nullable on error) is the serialized ``Answer``.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _episode_fk() -> sa.Column:
    return sa.Column(
        "episode_id",
        sa.Text(),
        sa.ForeignKey("episodes.id", ondelete="CASCADE"),
        nullable=False,
    )


def _created_at() -> sa.Column:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


def upgrade() -> None:
    op.create_table(
        "artifacts",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), primary_key=True),
        _episode_fk(),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("answer", postgresql.JSONB(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("tokens_in", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_out", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=False, server_default="0"),
        _created_at(),
        sa.UniqueConstraint(
            "episode_id", "kind", "prompt_version", name="uq_artifacts_episode_kind_version"
        ),
        sa.CheckConstraint("kind IN ('tldr', 'takeaways', 'verdict')", name="ck_artifacts_kind"),
    )

    op.create_table(
        "requests",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", sa.Text(), nullable=True),
        _episode_fk(),
        sa.Column("feature", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=True),
        sa.Column("question", sa.Text(), nullable=True),
        sa.Column("answer", postgresql.JSONB(), nullable=True),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("tokens_in", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_out", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_cached", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=False, server_default="0"),
        sa.Column("cached", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        _created_at(),
        sa.CheckConstraint("feature IN ('ask', 'preset', 'chat')", name="ck_requests_feature"),
    )
    op.create_index(
        "ix_requests_episode_created", "requests", ["episode_id", sa.text("created_at DESC")]
    )


def downgrade() -> None:
    op.drop_index("ix_requests_episode_created", table_name="requests")
    op.drop_table("requests")
    op.drop_table("artifacts")
