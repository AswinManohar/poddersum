"""PodderSum FastAPI application: /healthz plus the contract v0 routes (``api.routes``)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from agent.checkpointer import checkpointer_lifespan
from api import openapi
from api.deps import ApiError, api_error_handler
from api.routes import ROUTERS
from core.settings import Settings, get_settings
from db.session import create_engine, create_sessionmaker
from discovery.feed import MAX_REDIRECTS, USER_AGENT
from ingest.audio import make_download_client
from ingest.runner import IngestRunner, default_transcriber_factory, install_runner

logger = logging.getLogger(__name__)

DB_HEALTH_TIMEOUT_S = 2.0


def _health_engine(settings: Settings) -> AsyncEngine:
    # NullPool: a health probe should not hold connections or bind a pool to one event loop.
    return create_async_engine(
        settings.database_url_async,
        poolclass=NullPool,
        connect_args={"timeout": DB_HEALTH_TIMEOUT_S},
    )


async def check_database(engine: AsyncEngine) -> bool:
    try:
        async with asyncio.timeout(DB_HEALTH_TIMEOUT_S):
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:  # noqa: BLE001 - health check reports, never raises
        logger.warning("database health check failed: %s", type(exc).__name__)
        return False


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the app. Settings are validated here, so a missing variable fails at boot."""
    settings = settings or get_settings()
    engine = _health_engine(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        logger.info("PodderSum API starting (APP_ENV=%s)", settings.APP_ENV)
        # Tests may preset app.state.sessionmaker (bound to a throwaway schema).
        db_engine = None
        if getattr(app.state, "sessionmaker", None) is None:
            db_engine = create_engine(settings)
            app.state.engine = db_engine
            app.state.sessionmaker = create_sessionmaker(db_engine)
        app.state.http_client = httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT},
            max_redirects=MAX_REDIRECTS,
            timeout=httpx.Timeout(15.0, connect=5.0),
        )
        download_client = make_download_client(USER_AGENT)
        # Ingest runner (in-process; Inngest replaces it in P2-S1). Tests may preset
        # app.state.transcriber_factory to inject a fake STT vendor.
        runner = IngestRunner(
            app.state.sessionmaker,
            app.state.http_client,
            getattr(app.state, "transcriber_factory", None)
            or default_transcriber_factory(download_client),
        )
        app.state.ingest_runner = runner
        install_runner(runner)
        # Jobs left queued/running by a dead process can never finish: fail them so the
        # UI can retry (see ingest/runner.py).
        try:
            await runner.recover_interrupted()
        except Exception:  # noqa: BLE001 - a recovery problem must not block startup
            logger.exception("ingest job recovery failed")
        try:
            async with AsyncExitStack() as stack:
                # LangGraph checkpointer (episode chat history). Tests may preset
                # app.state.checkpointer (bound to a throwaway schema); a preset saver is
                # owned by the caller and not closed here.
                if getattr(app.state, "checkpointer", None) is None:
                    app.state.checkpointer = await stack.enter_async_context(
                        checkpointer_lifespan(conninfo=settings.database_url_libpq)
                    )
                    stack.callback(setattr, app.state, "checkpointer", None)
                yield
        finally:
            await runner.shutdown()
            install_runner(None)
            await download_client.aclose()
            await app.state.http_client.aclose()
            if db_engine is not None:
                await db_engine.dispose()
            await engine.dispose()

    app = FastAPI(title="PodderSum API", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.db_engine = engine

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.add_exception_handler(ApiError, api_error_handler)
    for router in ROUTERS:
        app.include_router(router)
    openapi.install(app)

    @app.get("/healthz", tags=["meta"])
    async def healthz(request: Request) -> dict[str, str]:
        ok = await check_database(request.app.state.db_engine)
        return {
            "status": "ok",
            "env": request.app.state.settings.APP_ENV,
            "db": "ok" if ok else "down",
        }

    return app


app = create_app()
