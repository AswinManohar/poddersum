"""The LangGraph checkpointer: ``AsyncPostgresSaver`` on the app's own Postgres database.

One process-wide saver backed by a small ``psycopg_pool.AsyncConnectionPool``. The URL is
``settings.database_url_libpq`` (``DATABASE_URL`` rewritten to plain ``postgresql://``,
whatever driver suffix it carries, e.g. ``+asyncpg``). No new env var.

Lifecycle (FastAPI)::

    from agent.checkpointer import checkpointer_lifespan

    @asynccontextmanager
    async def lifespan(app):
        async with checkpointer_lifespan() as checkpointer:
            app.state.checkpointer = checkpointer
            yield

or call :func:`open_checkpointer` / :func:`close_checkpointer` yourself. ``open`` is
idempotent and runs ``.setup()`` (LangGraph's own idempotent table migrations:
``checkpoints``, ``checkpoint_blobs``, ``checkpoint_writes``, ``checkpoint_migrations``)
once per opened saver. The tables live in the connection's default schema.

Thread ids are made only by :func:`thread_id_for` so P3-S6 can switch the key to
``u:{user_id}:ep:{episode_id}`` in one place.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from core.settings import get_settings

log = logging.getLogger(__name__)

POOL_MIN_SIZE = 1
POOL_MAX_SIZE = 5

_saver: AsyncPostgresSaver | None = None
_pool: AsyncConnectionPool | None = None
_lock = asyncio.Lock()


def thread_id_for(episode_id: str) -> str:
    """Checkpointer thread for an episode's chat (one shared thread per episode in M0)."""
    return f"ep:{episode_id}"


async def open_checkpointer(*, conninfo: str | None = None) -> AsyncPostgresSaver:
    """The process-wide saver, opened (and its tables set up) on first call.

    ``conninfo`` overrides the URL from settings (tests pin a throwaway schema with it);
    it is only used when the saver is not open yet."""
    global _saver, _pool
    async with _lock:
        if _saver is not None:
            return _saver
        pool = AsyncConnectionPool(
            conninfo=conninfo or get_settings().database_url_libpq,
            min_size=POOL_MIN_SIZE,
            max_size=POOL_MAX_SIZE,
            # Required by AsyncPostgresSaver: autocommit, dict rows, no server-side
            # prepared statements (safe behind PgBouncer-style poolers).
            kwargs={"autocommit": True, "row_factory": dict_row, "prepare_threshold": 0},
            open=False,
        )
        await pool.open(wait=True)
        try:
            saver = AsyncPostgresSaver(pool)  # type: ignore[arg-type]
            await saver.setup()
        except BaseException:
            await pool.close()
            raise
        _pool, _saver = pool, saver
        log.info("checkpointer opened")
        return saver


async def close_checkpointer() -> None:
    """Close the saver's pool; a later :func:`open_checkpointer` opens a fresh one."""
    global _saver, _pool
    async with _lock:
        pool, _pool, _saver = _pool, None, None
        if pool is not None:
            await pool.close()
            log.info("checkpointer closed")


@asynccontextmanager
async def checkpointer_lifespan(
    *, conninfo: str | None = None
) -> AsyncIterator[AsyncPostgresSaver]:
    """Open the checkpointer for the app's lifetime and close it on shutdown."""
    saver = await open_checkpointer(conninfo=conninfo)
    try:
        yield saver
    finally:
        await close_checkpointer()


__all__ = ["checkpointer_lifespan", "close_checkpointer", "open_checkpointer", "thread_id_for"]
