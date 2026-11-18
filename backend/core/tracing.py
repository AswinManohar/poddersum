"""LangSmith tracing helpers, configured explicitly from settings.

The repo ``.env`` is read by pydantic-settings and is not exported into ``os.environ``,
so the LangSmith ``Client`` is built from ``Settings`` rather than from env vars.

Guarantees:
- Tracing is off unless ``LANGSMITH_TRACING`` is true AND ``LANGSMITH_API_KEY`` is set.
  When off, no client is constructed, nothing is logged, nothing touches the network.
- Any tracing failure is swallowed (debug log only); it never breaks the traced call.
- Callers are responsible for passing sanitized inputs (no secrets, no raw audio).

Usage::

    tracer = get_tracer()
    async with tracer.run("llm.chat", inputs={...}, tags=[...], metadata={...}) as run:
        ...
        run.end(outputs={...}, metadata={...})
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import Any

from core.settings import Settings, get_settings

log = logging.getLogger(__name__)

ClientFactory = Callable[[Settings], Any]


def tracing_configured(settings: Settings) -> bool:
    return bool(settings.LANGSMITH_TRACING and settings.LANGSMITH_API_KEY is not None)


def _default_client_factory(settings: Settings) -> Any:
    from langsmith import Client

    assert settings.LANGSMITH_API_KEY is not None
    return Client(
        api_url=settings.LANGSMITH_ENDPOINT or None,
        api_key=settings.LANGSMITH_API_KEY.get_secret_value(),
    )


class TraceRun:
    """Handle for one traced run. ``end()`` records outputs; safe to call when disabled."""

    def __init__(self, run_tree: Any | None = None) -> None:
        self._run = run_tree
        self._outputs: dict[str, Any] | None = None
        self._metadata: dict[str, Any] = {}

    @property
    def active(self) -> bool:
        return self._run is not None

    @property
    def run_id(self) -> str | None:
        return str(self._run.id) if self._run is not None else None

    def end(
        self, outputs: Mapping[str, Any] | None = None, metadata: Mapping[str, Any] | None = None
    ) -> None:
        if outputs is not None:
            self._outputs = dict(outputs)
        if metadata:
            self._metadata.update(metadata)

    def _finish(self, error: str | None) -> None:
        if self._run is None:
            return
        try:
            self._run.end(outputs=self._outputs, error=error, metadata=self._metadata or None)
            self._run.patch()
        except Exception:  # tracing must never break the call
            log.debug("langsmith: failed to end run", exc_info=True)


class Tracer:
    """Creates LangSmith runs for gateway calls, or does nothing when disabled."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client_factory: ClientFactory | None = None,
    ) -> None:
        self._settings = settings
        self._client_factory = client_factory or _default_client_factory
        self._client: Any | None = None
        self._client_failed = False

    @property
    def settings(self) -> Settings:
        if self._settings is None:
            self._settings = get_settings()
        return self._settings

    @property
    def enabled(self) -> bool:
        return tracing_configured(self.settings) and not self._client_failed

    @property
    def project_name(self) -> str | None:
        return self.settings.LANGSMITH_PROJECT or None

    def client(self) -> Any | None:
        if not self.enabled:
            return None
        if self._client is None:
            try:
                self._client = self._client_factory(self.settings)
            except Exception:
                self._client_failed = True
                log.debug("langsmith: client construction failed; tracing off", exc_info=True)
                return None
        return self._client

    def _start(
        self,
        name: str,
        run_type: str,
        inputs: Mapping[str, Any],
        tags: list[str],
        metadata: Mapping[str, Any],
    ) -> TraceRun:
        client = self.client()
        if client is None:
            return TraceRun()
        try:
            from langsmith.run_helpers import get_current_run_tree
            from langsmith.run_trees import RunTree

            extra = {"metadata": dict(metadata)}
            parent = get_current_run_tree()
            if parent is not None:
                run = parent.create_child(
                    name=name, run_type=run_type, inputs=dict(inputs), tags=tags, extra=extra
                )
            else:
                kwargs: dict[str, Any] = {}
                if self.project_name:
                    kwargs["project_name"] = self.project_name
                run = RunTree(
                    name=name,
                    run_type=run_type,
                    inputs=dict(inputs),
                    tags=tags,
                    extra=extra,
                    ls_client=client,
                    **kwargs,
                )
            run.post()
            return TraceRun(run)
        except Exception:
            log.debug("langsmith: failed to start run", exc_info=True)
            return TraceRun()

    @asynccontextmanager
    async def run(
        self,
        name: str,
        *,
        run_type: str = "llm",
        inputs: Mapping[str, Any] | None = None,
        tags: list[str] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> AsyncIterator[TraceRun]:
        handle = (
            self._start(name, run_type, inputs or {}, list(tags or []), metadata or {})
            if self.enabled
            else TraceRun()
        )
        try:
            yield handle
        except BaseException as exc:
            handle._finish(error=f"{type(exc).__name__}: {exc}"[:2000])
            raise
        else:
            handle._finish(error=None)

    def flush(self) -> None:
        """Block until queued runs are sent. No-op when tracing is off."""
        if self._client is None:
            return
        try:
            self._client.flush()
        except Exception:
            log.debug("langsmith: flush failed", exc_info=True)


@lru_cache
def get_tracer() -> Tracer:
    return Tracer()
