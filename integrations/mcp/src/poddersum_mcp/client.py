"""Async HTTP client for the PodderSum API (contract v0, ``backend/api/schemas.py``).

Responses are returned as plain dicts shaped like the contract models; this package never
imports backend code, so it can be installed on its own.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx

from poddersum_mcp import __version__
from poddersum_mcp.config import START_HINT, Config
from poddersum_mcp.sse import parse_sse

JSON = dict[str, Any]
# The job stream pings every 15 s and the answer stream sends status events; 120 s of
# silence means the server is stuck.
STREAM_READ_TIMEOUT_S = 120.0


class PodderSumError(Exception):
    """A failure the tool should report to the model as a readable message."""

    def __init__(self, message: str, *, code: str = "error", status: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status


def is_model_unavailable(exc: PodderSumError) -> bool:
    """The backend's own model failed or is not configured (any ``llm_*`` code, or 503)."""
    return exc.code.startswith("llm_") or exc.status == 503


class ApiUnreachable(PodderSumError):
    def __init__(self, api_url: str) -> None:
        super().__init__(
            f"PodderSum API not reachable at {api_url}; start it with `{START_HINT}` "
            "(and the database with `docker compose -f infra/docker-compose.yml up -d`), "
            "or set PODDERSUM_API_URL to a running instance.",
            code="api_unreachable",
        )


def _error_from_response(resp: httpx.Response) -> PodderSumError:
    code, detail = "http_error", resp.reason_phrase or f"HTTP {resp.status_code}"
    try:
        body = resp.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        if isinstance(body.get("detail"), str):
            detail = body["detail"]
            code = str(body.get("code") or code)
        elif resp.status_code == 422:
            code, detail = "invalid_request", "The API rejected the request parameters."
    return PodderSumError(detail, code=code, status=resp.status_code)


class PodderSumClient:
    """One instance per tool call or per process; wraps a single ``httpx.AsyncClient``."""

    def __init__(self, config: Config, *, transport: httpx.AsyncBaseTransport | None = None):
        headers = {"User-Agent": f"poddersum-mcp/{__version__}", "Accept": "application/json"}
        if config.api_key:
            headers["Authorization"] = f"Bearer {config.api_key}"
        self.config = config
        self._http = httpx.AsyncClient(
            base_url=config.api_url,
            headers=headers,
            timeout=httpx.Timeout(30.0, connect=5.0),
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> PodderSumClient:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    # --- plumbing -----------------------------------------------------------

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            resp = await self._http.request(method, path, **kwargs)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise ApiUnreachable(self.config.api_url) from exc
        except httpx.TimeoutException as exc:
            raise PodderSumError(
                f"PodderSum API at {self.config.api_url} timed out on {method} {path}.",
                code="timeout",
            ) from exc
        if resp.status_code >= 400:
            raise _error_from_response(resp)
        return resp.json()

    @asynccontextmanager
    async def _stream(
        self, method: str, path: str, *, headers: dict[str, str] | None = None, **kwargs: Any
    ) -> AsyncIterator[httpx.Response]:
        timeout = httpx.Timeout(30.0, connect=5.0, read=STREAM_READ_TIMEOUT_S)
        hdrs = {"Accept": "text/event-stream", **(headers or {})}
        try:
            async with self._http.stream(method, path, headers=hdrs, timeout=timeout, **kwargs) as resp:
                if resp.status_code >= 400:
                    await resp.aread()
                    raise _error_from_response(resp)
                yield resp
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise ApiUnreachable(self.config.api_url) from exc
        except httpx.ReadTimeout as exc:
            raise PodderSumError(
                f"The PodderSum stream {path} went silent for {STREAM_READ_TIMEOUT_S:.0f} s.",
                code="timeout",
            ) from exc

    # --- discovery ----------------------------------------------------------

    async def search(self, query: str, limit: int = 5) -> JSON:
        return await self._request("GET", "/search", params={"q": query, "limit": limit})

    async def list_episodes(self, podcast_id: str, limit: int = 10) -> JSON:
        return await self._request("GET", f"/podcasts/{podcast_id}/episodes", params={"limit": limit})

    async def get_episode(self, episode_id: str) -> JSON:
        return await self._request("GET", f"/episodes/{episode_id}")

    # --- jobs ---------------------------------------------------------------

    async def start_ingest(self, episode_id: str) -> JSON:
        return await self._request("POST", f"/episodes/{episode_id}/ingest")

    async def get_job(self, job_id: str) -> JSON:
        return await self._request("GET", f"/jobs/{job_id}")

    async def job_events(self, job_id: str, after_seq: int = 0) -> AsyncIterator[JSON]:
        """``JobEvent`` dicts until the terminal (succeeded/failed) event."""
        headers = {"Last-Event-ID": str(after_seq)} if after_seq else None
        async with self._stream("GET", f"/jobs/{job_id}/events", headers=headers) as resp:
            async for frame in parse_sse(resp.aiter_lines()):
                if frame.event != "job":
                    continue
                yield json.loads(frame.data)

    # --- answers ------------------------------------------------------------

    async def _answer_stream(self, path: str, body: JSON) -> AsyncIterator[JSON]:
        async with self._stream("POST", path, json=body) as resp:
            async for frame in parse_sse(resp.aiter_lines()):
                payload = json.loads(frame.data)
                yield payload
                if payload.get("event") == "done":
                    return

    def ask(self, episode_id: str, question: str) -> AsyncIterator[JSON]:
        return self._answer_stream(f"/episodes/{episode_id}/ask", {"question": question})

    def chat(self, episode_id: str, message: str) -> AsyncIterator[JSON]:
        return self._answer_stream(f"/episodes/{episode_id}/chat", {"message": message})

    async def get_artifact(self, episode_id: str, kind: str) -> JSON:
        # The first request generates the preset with a model call; allow for it.
        return await self._request(
            "GET",
            f"/episodes/{episode_id}/artifacts/{kind}",
            timeout=httpx.Timeout(180.0, connect=5.0),
        )

    async def get_transcript(
        self, episode_id: str, *, start_ms: int | None = None, end_ms: int | None = None, limit: int = 500
    ) -> JSON:
        """One page of the PROPOSED ``GET /episodes/{id}/transcript`` route (not in contract v0).

        Expected body: ``{episode_id, segments: [{id, idx, start_ms, end_ms, speaker, text}],
        total_segments, next_start_ms}``. A FastAPI "route not found" 404 (no ``code``) is
        reported as ``api_gap`` so tools can explain it instead of saying "unknown episode".
        """
        params: dict[str, int] = {"limit": limit}
        if start_ms is not None:
            params["start_ms"] = start_ms
        if end_ms is not None:
            params["end_ms"] = end_ms
        try:
            return await self._request("GET", f"/episodes/{episode_id}/transcript", params=params)
        except PodderSumError as exc:
            if exc.status in (404, 405) and exc.code == "http_error":
                raise PodderSumError(
                    "This PodderSum API has no transcript route yet (GET /episodes/{id}/transcript), "
                    "so the host model cannot read the transcript. Update the backend, or use the "
                    "server-side summary (engine='server') if its model is configured.",
                    code="api_gap",
                    status=exc.status,
                ) from exc
            raise

    async def chat_history(self, episode_id: str) -> list[JSON]:
        return await self._request("GET", f"/episodes/{episode_id}/chat")


async def collect_answer(events: AsyncIterator[JSON], on_status: Any = None) -> JSON:
    """Drain an ask/chat stream into the final ``Answer`` dict.

    Falls back to the streamed claims if the server closed without an ``answer`` frame.
    """
    claims: list[JSON] = []
    answer: JSON | None = None
    async for ev in events:
        kind = ev.get("event")
        if kind == "status" and on_status is not None:
            await on_status(ev.get("message") or "")
        elif kind == "claim":
            claims.append(ev["claim"])
        elif kind == "answer":
            answer = ev["answer"]
        elif kind == "error":
            raise PodderSumError(ev.get("message") or "The answer failed.", code=ev.get("code", "error"))
    if answer is None:
        if not claims:
            raise PodderSumError("The answer stream ended without an answer.", code="empty_answer")
        answer = {"claims": claims, "cached": False}
    return answer
