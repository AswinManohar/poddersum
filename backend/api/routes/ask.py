"""``POST /episodes/{episode_id}/ask``: a streamed, cited answer (SSE).

Checks that happen before the stream opens return JSON errors (404 unknown episode,
409 ``not_ready``). Everything after that, including model failures, arrives in the
stream as an ``error`` event followed by ``done`` (see ``agent.answer_graph``).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette import EventSourceResponse

from agent.answer_graph import stream_answer
from agent.deps import AgentDeps
from api.deps import ApiError, SessionDep, errors
from api.openapi import sse_doc
from api.routes.common import EpisodeIdPath
from api.schemas import ANSWER_STREAM_EVENT_NAMES, AnswerStreamEvent, AskRequest
from db import queries
from llm import get_gateway

router = APIRouter(tags=["answers"])

PING_INTERVAL_S = 15
ANSWER_STREAM_DOC = (
    "SSE stream. Each frame's `event:` name equals the `event` field of its JSON `data` "
    "(`status`, `claim`, `answer`, `error`, `done`). Order: status* claim* "
    "(answer | error) done. Not replayable."
)
ANSWER_STREAM_SSE = sse_doc(
    "AnswerStreamEvent", events=list(ANSWER_STREAM_EVENT_NAMES), description=ANSWER_STREAM_DOC
)


def agent_deps(request: Request) -> AgentDeps:
    """Answer-engine dependencies; tests may preset ``app.state.gateway`` (fake provider)."""
    gateway = getattr(request.app.state, "gateway", None) or get_gateway()
    return AgentDeps(sessionmaker=request.app.state.sessionmaker, gateway=gateway)


async def ensure_episode_ready(session: AsyncSession, episode_id: str) -> None:
    """404 for an unknown episode, 409 ``not_ready`` when it has no transcript yet."""
    if await queries.get_episode_with_podcast(session, episode_id) is None:
        raise ApiError(404, "not_found", "Unknown episode.")
    if not await queries.has_transcript(session, episode_id):
        raise ApiError(409, "not_ready", "This episode has no transcript yet; ingest it first.")


async def _sse_frames(events: AsyncIterator[AnswerStreamEvent]) -> AsyncIterator[dict[str, str]]:
    async for event in events:
        yield {"event": event.event, "data": event.model_dump_json()}


@router.post(
    "/episodes/{episode_id}/ask",
    responses=errors(404, 409),
    openapi_extra=ANSWER_STREAM_SSE,
    summary="Ask anything about an episode (streamed, cited answer)",
)
async def ask(
    episode_id: EpisodeIdPath, body: AskRequest, request: Request, session: SessionDep
) -> EventSourceResponse:
    """Answers from the stored transcript only; every claim cites a segment.
    409 when the episode has no transcript yet (`status` is not `ready`)."""
    await ensure_episode_ready(session, episode_id)
    await session.close()  # do not hold a pooled connection for the stream's lifetime
    events = stream_answer(agent_deps(request), episode_id, body.question)
    return EventSourceResponse(_sse_frames(events), ping=PING_INTERVAL_S)
