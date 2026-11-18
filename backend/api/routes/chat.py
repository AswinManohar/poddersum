"""``POST /episodes/{episode_id}/chat`` (SSE) and ``GET /episodes/{episode_id}/chat``.

The conversation lives in the LangGraph checkpointer (``app.state.checkpointer``, opened in
the app lifespan), one thread per episode (``agent.checkpointer.thread_id_for``). Checks
that happen before the stream opens return JSON errors (404 unknown episode, 409
``not_ready``); everything after that arrives in the stream, exactly as for ask.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from sse_starlette import EventSourceResponse

from agent.chat_graph import get_chat_history, stream_chat
from api.deps import ApiError, SessionDep, errors
from api.routes.ask import (
    ANSWER_STREAM_SSE,
    PING_INTERVAL_S,
    _sse_frames,
    agent_deps,
    ensure_episode_ready,
)
from api.routes.common import EpisodeIdPath
from api.schemas import ChatMessage, ChatRequest
from db import queries

router = APIRouter(tags=["chat"])


@router.post(
    "/episodes/{episode_id}/chat",
    responses=errors(404, 409),
    openapi_extra=ANSWER_STREAM_SSE,
    summary="Send a chat message about an episode (streamed, cited reply)",
)
async def send_chat(
    episode_id: EpisodeIdPath, body: ChatRequest, request: Request, session: SessionDep
) -> EventSourceResponse:
    """Same stream events as ask. On success the user message and the reply are persisted
    and appear in ``GET /episodes/{id}/chat``; a failed reply stores neither, so the
    client can resend. 409 when the episode has no transcript yet."""
    await ensure_episode_ready(session, episode_id)
    await session.close()  # do not hold a pooled connection for the stream's lifetime
    events = stream_chat(
        agent_deps(request), request.app.state.checkpointer, episode_id, body.message
    )
    return EventSourceResponse(_sse_frames(events), ping=PING_INTERVAL_S)


@router.get(
    "/episodes/{episode_id}/chat",
    response_model=list[ChatMessage],
    responses=errors(404),
    summary="Get an episode's chat history (oldest first)",
)
async def chat_history(
    episode_id: EpisodeIdPath, request: Request, session: SessionDep
) -> list[ChatMessage]:
    """Empty list when the episode has no chat yet (or no transcript)."""
    if await queries.get_episode_with_podcast(session, episode_id) is None:
        raise ApiError(404, "not_found", "Unknown episode.")
    await session.close()
    return await get_chat_history(request.app.state.checkpointer, episode_id)
