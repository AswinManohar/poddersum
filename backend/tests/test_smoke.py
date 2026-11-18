"""M0-S0 smoke tests: settings, database connectivity, /healthz."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from api.main import create_app
from core.settings import LOCAL_DATABASE_URL, Settings, SettingsError

# --- settings ---------------------------------------------------------------


def test_settings_load_explicit_values() -> None:
    s = Settings(
        _env_file=None,
        GEMINI_API_KEY="g-key",
        APP_ENV="staging",
        DATABASE_URL="postgresql://u:p@db:5432/x",
        LANGSMITH_TRACING="true",
        LANGSMITH_API_KEY="ls-key",
        LANGSMITH_PROJECT="proj",
        CORS_ORIGINS="https://a.example, https://b.example",
    )
    assert s.APP_ENV == "staging"
    assert s.GEMINI_API_KEY.get_secret_value() == "g-key"
    assert s.LANGSMITH_TRACING is True
    assert s.LANGSMITH_PROJECT == "proj"
    assert s.CORS_ORIGINS == ["https://a.example", "https://b.example"]


def test_env_vars_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@db/x")
    s = Settings(_env_file=None)
    assert s.APP_ENV == "production"
    assert s.database_url_libpq == "postgresql://u:p@db/x"


def test_local_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    s = Settings(_env_file=None, GEMINI_API_KEY="x")
    assert s.APP_ENV == "local"
    assert s.DATABASE_URL == LOCAL_DATABASE_URL
    assert s.CORS_ORIGINS == ["http://localhost:3000", "http://127.0.0.1:3000"]
    assert s.LANGSMITH_API_KEY is None


@pytest.mark.parametrize(
    "given",
    [
        "postgres://u:p@h:1/d",
        "postgresql://u:p@h:1/d",
        "postgresql+asyncpg://u:p@h:1/d",
        "postgresql+psycopg://u:p@h:1/d",
        "postgresql+psycopg2://u:p@h:1/d",
    ],
)
def test_database_url_helpers_normalise(given: str) -> None:
    s = Settings(_env_file=None, GEMINI_API_KEY="x", DATABASE_URL=given)
    assert s.database_url_async == "postgresql+asyncpg://u:p@h:1/d"
    assert s.database_url_sync_psycopg == "postgresql+psycopg://u:p@h:1/d"
    assert s.database_url_libpq == "postgresql://u:p@h:1/d"


def test_bad_database_scheme_fails() -> None:
    with pytest.raises(SettingsError, match="DATABASE_URL"):
        Settings(_env_file=None, GEMINI_API_KEY="x", DATABASE_URL="mysql://u:p@h/d")


def test_missing_gemini_key_names_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(SettingsError) as info:
        Settings(_env_file=None)
    assert "GEMINI_API_KEY" in str(info.value)
    assert "missing" in str(info.value)


def test_non_local_requires_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(SettingsError, match="DATABASE_URL"):
        Settings(_env_file=None, GEMINI_API_KEY="x", APP_ENV="production")


def test_invalid_app_env_fails() -> None:
    with pytest.raises(SettingsError, match="APP_ENV"):
        Settings(_env_file=None, GEMINI_API_KEY="x", APP_ENV="dev")


def test_secrets_not_in_repr() -> None:
    s = Settings(
        _env_file=None,
        GEMINI_API_KEY="super-secret-gemini",
        LANGSMITH_API_KEY="super-secret-langsmith",
        DATABASE_URL="postgresql://u:db-password-123@h/d",
    )
    for rendered in (repr(s), str(s), str(s.model_dump())):
        assert "super-secret-gemini" not in rendered
        assert "super-secret-langsmith" not in rendered
    assert "db-password-123" not in repr(s)


def test_error_message_never_contains_values() -> None:
    with pytest.raises(SettingsError) as info:
        Settings(_env_file=None, GEMINI_API_KEY="leak-me", APP_ENV="nope")
    assert "leak-me" not in str(info.value)
    assert "nope" not in str(info.value)


# --- database ---------------------------------------------------------------


def _postgres_reachable(url: str) -> bool:
    async def probe() -> bool:
        engine = create_async_engine(url, connect_args={"timeout": 2})
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return True
        except Exception:
            return False
        finally:
            await engine.dispose()

    return asyncio.run(probe())


_PG_UP = _postgres_reachable(LOCAL_DATABASE_URL)
requires_pg = pytest.mark.skipif(
    not _PG_UP,
    reason="local Postgres not reachable on localhost:5433; "
    "run `docker compose -f infra/docker-compose.yml up -d`",
)


@pytest.mark.db
@requires_pg
async def test_database_select_one(settings: Settings) -> None:
    engine = create_async_engine(settings.database_url_async)
    try:
        async with engine.connect() as conn:
            assert (await conn.execute(text("SELECT 1"))).scalar_one() == 1
    finally:
        await engine.dispose()


@pytest.mark.db
@requires_pg
async def test_database_pgvector_available(settings: Settings) -> None:
    engine = create_async_engine(settings.database_url_async)
    try:
        async with engine.connect() as conn:
            row = await conn.execute(
                text("SELECT 1 FROM pg_available_extensions WHERE name = 'vector'")
            )
            assert row.scalar_one_or_none() == 1
    finally:
        await engine.dispose()


@pytest.mark.db
@requires_pg
async def test_database_psycopg_libpq_url(settings: Settings) -> None:
    import psycopg

    async with await psycopg.AsyncConnection.connect(settings.database_url_libpq) as conn:
        cur = await conn.execute("SELECT 1")
        assert (await cur.fetchone()) == (1,)


# --- /healthz ---------------------------------------------------------------


async def _get_healthz(settings: Settings) -> httpx.Response:
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get("/healthz")


@requires_pg
async def test_healthz_db_ok(settings: Settings) -> None:
    resp = await _get_healthz(settings)
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "env": "local", "db": "ok"}


async def test_healthz_db_down_still_200() -> None:
    s = Settings(
        _env_file=None,
        GEMINI_API_KEY="x",
        DATABASE_URL="postgresql://nobody:nothing@127.0.0.1:1/none",
    )
    resp = await _get_healthz(s)
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "env": "local", "db": "down"}


async def test_cors_allows_local_web_origin(settings: Settings) -> None:
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.options(
            "/healthz",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:3000"
