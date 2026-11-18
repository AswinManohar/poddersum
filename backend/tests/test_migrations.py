"""Alembic upgrade/downgrade round trip in a throwaway schema."""

from __future__ import annotations

import uuid

import psycopg
import pytest
from alembic import command

from tests.conftest import (
    LOCAL_SETTINGS,
    PG_SKIP_REASON,
    PG_UP,
    alembic_config,
    create_schema,
    drop_schema,
)

pytestmark = [pytest.mark.db, pytest.mark.skipif(not PG_UP, reason=PG_SKIP_REASON)]


def _tables(schema: str) -> set[str]:
    with psycopg.connect(LOCAL_SETTINGS.database_url_libpq) as conn:
        rows = conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = %s", (schema,)
        ).fetchall()
    return {r[0] for r in rows}


def test_upgrade_downgrade_round_trip() -> None:
    schema = f"pytest_mig_{uuid.uuid4().hex[:8]}"
    create_schema(schema)
    try:
        cfg = alembic_config(schema)
        command.upgrade(cfg, "head")
        assert {"podcasts", "episodes", "artifacts", "requests", "alembic_version"} <= _tables(
            schema
        )
        command.downgrade(cfg, "0002")
        assert not {"artifacts", "requests"} & _tables(schema)
        assert {"transcripts", "segments", "jobs"} <= _tables(schema)
        command.downgrade(cfg, "base")
        assert _tables(schema) == {"alembic_version"}
        command.upgrade(cfg, "head")
        assert {"podcasts", "episodes"} <= _tables(schema)
        with psycopg.connect(LOCAL_SETTINGS.database_url_libpq) as conn:
            idx = conn.execute(
                "SELECT indexdef FROM pg_indexes WHERE schemaname = %s AND indexname = %s",
                (schema, "ix_episodes_podcast_published"),
            ).fetchone()
        assert idx and "published_at DESC NULLS LAST" in idx[0]
    finally:
        drop_schema(schema)
