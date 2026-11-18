"""Postgres advisory lock per episode (prototype; Redis lock arrives in Phase 1).

The lock is session-level and lives on a DEDICATED connection (autocommit, never returned
to the pool while held), so it is held for the whole job regardless of the job's own
transactions and released in ``finally``. If the process dies, Postgres drops the
connection and the lock with it. This is a second guard next to the partial unique index
``uq_jobs_active_ingest`` (one active job per episode).
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

LOCK_NAMESPACE = "poddersum:ingest:"


class LockNotAcquired(RuntimeError):
    """Another worker holds the episode's ingest lock."""


def episode_lock_key(episode_id: str) -> int:
    """Stable signed 64-bit key: first 8 bytes of sha256(namespace + episode id)."""
    digest = hashlib.sha256((LOCK_NAMESPACE + episode_id).encode()).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


@asynccontextmanager
async def episode_lock(
    engine: AsyncEngine,
    episode_id: str,
    *,
    wait_s: float = 0.0,
    poll_s: float = 0.5,
) -> AsyncIterator[None]:
    """Hold ``pg_try_advisory_lock`` for ``episode_id``; retry for up to ``wait_s``.

    Raises :class:`LockNotAcquired` when another session still holds it.
    """
    key = episode_lock_key(episode_id)
    conn = await engine.connect()
    try:
        conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + wait_s
        while True:
            got = (
                await conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": key})
            ).scalar_one()
            if got:
                break
            if loop.time() >= deadline:
                raise LockNotAcquired(episode_id)
            await asyncio.sleep(poll_s)
        try:
            yield
        finally:
            await asyncio.shield(conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key}))
    finally:
        await asyncio.shield(conn.close())


async def lock_is_free(engine: AsyncEngine, episode_id: str) -> bool:
    """True when nobody holds the episode lock (checked by briefly taking it)."""
    try:
        async with episode_lock(engine, episode_id):
            return True
    except LockNotAcquired:
        return False
