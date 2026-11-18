"""Shared test setup. Never depends on the real GEMINI_API_KEY value."""

from __future__ import annotations

import os

# Set before any app import: api.main builds a module-level app at import time.
os.environ["GEMINI_API_KEY"] = "test-dummy-key"

import pytest  # noqa: E402

from core.settings import Settings, get_settings  # noqa: E402


@pytest.fixture(autouse=True)
def _dummy_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-dummy-key")
    get_settings.cache_clear()


@pytest.fixture
def settings() -> Settings:
    """Settings built without reading the real .env."""
    return Settings(_env_file=None, GEMINI_API_KEY="test-dummy-key")


# --- database + API fixtures (M0-S1) ------------------------------------------
#
# DB tests never touch the dev tables: a throwaway schema ``pytest_<random>`` is created
# once per session, migrated with Alembic (search_path pinned to it), truncated before
# every test and dropped at the end. Skipped with a reason when Postgres is unreachable.

import uuid  # noqa: E402
from collections.abc import AsyncIterator, Iterator  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402

import httpx  # noqa: E402
import psycopg  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402

from core.settings import LOCAL_DATABASE_URL  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
BACKEND_DIR = Path(__file__).resolve().parents[1]
LOCAL_SETTINGS = Settings(_env_file=None, GEMINI_API_KEY="x", DATABASE_URL=LOCAL_DATABASE_URL)


def _pg_reachable() -> bool:
    try:
        with psycopg.connect(LOCAL_SETTINGS.database_url_libpq, connect_timeout=2) as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


PG_UP = _pg_reachable()
PG_SKIP_REASON = (
    "local Postgres not reachable on localhost:5433; "
    "run `docker compose -f infra/docker-compose.yml up -d`"
)


def alembic_config(schema: str):
    from alembic.config import Config

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.attributes["configure_logger"] = False
    cfg.set_main_option(
        "sqlalchemy.url",
        (LOCAL_SETTINGS.database_url_sync_psycopg + f"?options=-csearch_path%3D{schema}").replace(
            "%", "%%"
        ),
    )
    return cfg


def create_schema(schema: str) -> None:
    with psycopg.connect(LOCAL_SETTINGS.database_url_libpq, autocommit=True) as conn:
        conn.execute(f'CREATE SCHEMA "{schema}"')


def drop_schema(schema: str) -> None:
    with psycopg.connect(LOCAL_SETTINGS.database_url_libpq, autocommit=True) as conn:
        conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')


@pytest.fixture(scope="session")
def db_schema() -> Iterator[str]:
    if not PG_UP:
        pytest.skip(PG_SKIP_REASON)
    from alembic import command

    schema = f"pytest_{uuid.uuid4().hex[:10]}"
    create_schema(schema)
    try:
        command.upgrade(alembic_config(schema), "head")
        yield schema
    finally:
        drop_schema(schema)


@pytest.fixture
async def db_sessionmaker(db_schema: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        LOCAL_SETTINGS.database_url_async,
        connect_args={"server_settings": {"search_path": db_schema}},
    )
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE requests, artifacts, job_events, jobs, segments, transcripts, "
                "episodes, podcasts CASCADE"
            )
        )
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


def checkpointer_conninfo(schema: str) -> str:
    """libpq URL with search_path pinned, so LangGraph's tables land in the test schema."""
    return f"{LOCAL_SETTINGS.database_url_libpq}?options=-csearch_path%3D{schema}"


CHECKPOINT_TABLES = "checkpoints, checkpoint_blobs, checkpoint_writes"


@pytest.fixture
async def checkpointer(db_schema: str) -> AsyncIterator[Any]:
    """The process-wide LangGraph saver opened on the test schema, with empty threads.

    Preset it as ``app.state.checkpointer``; the app lifespan then neither opens nor
    closes it. Reopened per test: a saver's pool is bound to the test's event loop."""
    from agent import checkpointer as cp

    await cp.close_checkpointer()
    saver = await cp.open_checkpointer(conninfo=checkpointer_conninfo(db_schema))
    async with saver.conn.connection() as conn:  # type: ignore[union-attr]
        await conn.execute(f"TRUNCATE {CHECKPOINT_TABLES}")
    try:
        yield saver
    finally:
        await cp.close_checkpointer()


@pytest.fixture
async def app(
    settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    checkpointer: Any,
) -> AsyncIterator[FastAPI]:
    """The real app (lifespan running) with sessions bound to the test schema."""
    from api.main import create_app
    from db.session import get_session

    application = create_app(settings)
    # Runner + SSE use app.state.sessionmaker: bind it to the test schema too.
    application.state.sessionmaker = db_sessionmaker
    application.state.checkpointer = checkpointer

    async def _session() -> AsyncIterator[AsyncSession]:
        async with db_sessionmaker() as session:
            yield session

    application.dependency_overrides[get_session] = _session
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
