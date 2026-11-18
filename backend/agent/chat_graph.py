"""Episode chat: one LangGraph run per user message, history kept by the Postgres checkpointer.

Graph (compiled with the checkpointer; thread ``agent.checkpointer.thread_id_for``)::

    START -> generate -> record_turns -> persist -> END
    (an error in generate skips record_turns)

* ``generate``: loads the episode's stored segments (never audio), builds the messages
  with the SAME prefix as ask (system prompt + full transcript, see
  ``agent.transcript_context``) followed by the ``chat`` task, which carries the last
  :data:`MAX_PRIOR_MESSAGES` turns and the new message. Streams the NDJSON reply through
  ``Gateway.stream`` on task ``chat``; each completed claim is grounded with
  ``agent.citations`` and, if it survives, sent as a ``ClaimEvent`` right away. The same
  pure validator over all claims then gives the final reply (identical to what was
  streamed); zero claims -> ``no_grounded_claims``.
* ``record_turns``: appends the user turn and the assistant turn (both with a UUID and
  ``created_at``) to ``turns``, the only persisted conversation state.
* ``persist``: one ``requests`` row (feature ``chat``) for success and failure alike, with
  tokens, cached tokens, cost, model, latency and prompt version.

Failed turns: when the reply fails (no grounded claims, model error, no transcript),
NEITHER the user message NOR an assistant turn is stored. History therefore always
alternates user/assistant, and the client can simply resend the message. The failure is
still visible in ``requests`` (``question`` + ``error``).

State and checkpoints: every channel of the state is checkpointed, so per-run values are
kept small and JSON-plain (no segments, no pydantic objects) and are reset by each run's
input. ``turns`` uses an append reducer. History in the prompt is bounded, so the cost of
a turn stays flat however long the chat gets; the full history stays in the checkpoint.

Concurrency: messages to the same thread are serialised by an in-process lock so two
quick messages cannot both start from the same checkpoint and drop a turn. Across API
processes this is not guaranteed yet (P2 moves the API to multiple workers; revisit then).

Expected failures never raise out of :func:`stream_chat`; event order is always
``status* claim* (answer | error) done``.
"""

from __future__ import annotations

import asyncio
import logging
import operator
import time
import uuid
import weakref
from collections.abc import AsyncIterator, Iterable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Annotated, Any, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime
from langgraph.types import StreamWriter

from agent import errors
from agent.checkpointer import thread_id_for
from agent.citations import ModelClaim, ground_claim, ground_claims, segments_by_id
from agent.deps import CHAT_TASK, AgentDeps
from agent.errors import AgentError
from agent.ndjson import NdjsonClaimParser
from agent.prompts import answer_version, load_prompt
from agent.transcript_context import (
    SegmentRef,
    TranscriptTooLongError,
    build_messages,
    ensure_fits,
)
from api.schemas import (
    Answer,
    AnswerEvent,
    AnswerStreamEvent,
    ChatMessage,
    ChatRole,
    Claim,
    ClaimEvent,
    DoneEvent,
    ErrorEvent,
    StatusEvent,
)
from db import queries
from llm import CallMetadata, LLMError, LLMResult

log = logging.getLogger(__name__)

CHAT_PROMPT = load_prompt("chat")
PROMPT_VERSION = answer_version(CHAT_PROMPT)  # chat-v1+system-v1
FEATURE = "chat"

MAX_PRIOR_MESSAGES = 8
"""Most recent stored messages (4 user/assistant exchanges) sent with a new message.
Bounds prompt size, so a turn's cost does not grow with the length of the chat."""

INTERNAL_ERROR_MESSAGE = "Something went wrong while replying. Please try again."
EMPTY_HISTORY = "(no earlier messages)"


class Turn(TypedDict):
    """One stored chat message (JSON-plain so the checkpoint serialiser stays simple)."""

    id: str  # UUID
    role: str  # ChatRole value
    text: str
    claims: list[dict[str, Any]]  # Claim.model_dump(mode="json"); empty for user turns
    created_at: str  # ISO 8601, UTC


class ChatState(TypedDict, total=False):
    # persisted conversation
    turns: Annotated[list[Turn], operator.add]
    # per-run values (reset by every run's input)
    episode_id: str
    message: str
    received_at: str  # when the user message arrived (ISO, UTC)
    started: float  # time.perf_counter() at request start (latency)
    claims: list[dict[str, Any]] | None  # grounded reply claims (Claim JSON)
    llm: dict[str, Any] | None  # model, tokens_in/out/cached, cost_usd
    error: dict[str, str] | None  # AgentError as {code, message, detail}


# --------------------------------------------------------------------------- prompt


def render_history(turns: Sequence[Turn]) -> str:
    """The last :data:`MAX_PRIOR_MESSAGES` turns as plain text for the chat prompt."""
    recent = list(turns)[-MAX_PRIOR_MESSAGES:]
    if not recent:
        return EMPTY_HISTORY
    lines = []
    for turn in recent:
        if turn["role"] == ChatRole.user.value:
            lines.append(f"User: {_one_line(turn['text'])}")
            continue
        lines.append("Assistant:")
        for claim in turn["claims"]:
            ids = ", ".join(str(c["segment_id"]) for c in claim["citations"])
            lines.append(f"- {_one_line(claim['text'])} (cited: {ids})")
    return "\n".join(lines)


def build_chat_messages(
    segments: Sequence[SegmentRef], turns: Sequence[Turn], message: str
) -> list[dict[str, Any]]:
    """Same system + transcript prefix as ask, then the chat task (history + message)."""
    task = CHAT_PROMPT.render(history=render_history(turns), message=message)
    return build_messages(segments, task)


def _one_line(text: str) -> str:
    return " ".join(text.split())


# --------------------------------------------------------------------------- nodes


async def generate(state: ChatState, runtime: Runtime[AgentDeps]) -> ChatState:
    deps = runtime.context
    episode_id = state["episode_id"]
    segments = await deps.load_segments(episode_id)
    if not segments:
        return {"error": _error_dict(errors.not_ready())}
    messages = build_chat_messages(segments, state.get("turns", []), state["message"])
    context_window, reserved_output = deps.context_limits(CHAT_TASK)
    try:
        ensure_fits(messages, context_window=context_window, reserved_output=reserved_output)
    except TranscriptTooLongError as exc:
        return {"error": _error_dict(AgentError(errors.TRANSCRIPT_TOO_LONG, str(exc)))}

    writer = runtime.stream_writer
    writer(StatusEvent(message="Writing the reply"))
    by_id = segments_by_id(segments)
    parser = NdjsonClaimParser()
    raw_claims: list[ModelClaim] = []
    streamed = 0
    result: LLMResult | None = None
    metadata = CallMetadata(feature=FEATURE, prompt_version=PROMPT_VERSION, episode_id=episode_id)
    try:
        async for item in deps.gateway.stream(CHAT_TASK, messages, metadata=metadata):
            if isinstance(item, LLMResult):
                result = item
            else:
                streamed = _stream_valid(parser.feed(item), raw_claims, by_id, writer, streamed)
        streamed = _stream_valid(parser.close(), raw_claims, by_id, writer, streamed)
    except LLMError as exc:
        log.warning("chat generation failed: %s", exc)
        return {"error": _error_dict(errors.from_llm_error(exc))}
    assert result is not None, "Gateway.stream always ends with an LLMResult"

    llm = {
        "model": result.model,
        "tokens_in": result.tokens_in,
        "tokens_out": result.tokens_out,
        "tokens_cached": result.tokens_cached,
        "cost_usd": result.cost_usd,
    }
    claims = ground_claims(raw_claims, by_id)
    if dropped := len(raw_claims) - len(claims):
        log.info("chat: dropped %d of %d claims", dropped, len(raw_claims))
    if not claims:
        return {"llm": llm, "error": _error_dict(errors.no_grounded_claims())}
    return {"llm": llm, "claims": [c.model_dump(mode="json") for c in claims]}


def _stream_valid(
    new: Iterable[ModelClaim],
    raw_claims: list[ModelClaim],
    segments: Mapping[int, SegmentRef],
    writer: StreamWriter,
    streamed: int,
) -> int:
    for raw in new:
        raw_claims.append(raw)
        claim = ground_claim(raw, segments)
        if claim is not None:
            writer(ClaimEvent(index=streamed, claim=claim))
            streamed += 1
    return streamed


def record_turns(state: ChatState) -> ChatState:
    claims = state["claims"] or []
    user: Turn = {
        "id": str(uuid.uuid4()),
        "role": ChatRole.user.value,
        "text": state["message"],
        "claims": [],
        "created_at": state["received_at"],
    }
    assistant: Turn = {
        "id": str(uuid.uuid4()),
        "role": ChatRole.assistant.value,
        "text": " ".join(c["text"] for c in claims),
        "claims": claims,
        "created_at": _now_iso(),
    }
    return {"turns": [user, assistant]}


async def persist(state: ChatState, runtime: Runtime[AgentDeps]) -> ChatState:
    llm = state.get("llm") or {}
    error = state.get("error")
    answer = None if error else _answer(state).model_dump(mode="json")
    async with runtime.context.sessionmaker() as session:
        await queries.record_request(
            session,
            episode_id=state["episode_id"],
            feature=FEATURE,
            question=state["message"],
            answer=answer,
            prompt_version=PROMPT_VERSION,
            model=llm.get("model"),
            tokens_in=llm.get("tokens_in", 0),
            tokens_out=llm.get("tokens_out", 0),
            tokens_cached=llm.get("tokens_cached", 0),
            cost_usd=llm.get("cost_usd", 0),
            cached=False,
            latency_ms=int((time.perf_counter() - state["started"]) * 1000),
            error=error["detail"] if error else None,
        )
        await session.commit()
    return {}


# --------------------------------------------------------------------------- wiring


def _after_generate(state: ChatState) -> str:
    return "persist" if state.get("error") else "record_turns"


def chat_graph_builder() -> StateGraph:
    return (
        StateGraph(ChatState, context_schema=AgentDeps)
        .add_node("generate", generate)
        .add_node("record_turns", record_turns)
        .add_node("persist", persist)
        .add_edge(START, "generate")
        .add_conditional_edges("generate", _after_generate, ["record_turns", "persist"])
        .add_edge("record_turns", "persist")
        .add_edge("persist", END)
    )


_BUILDER = chat_graph_builder()
_thread_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()


def chat_graph(checkpointer: BaseCheckpointSaver) -> CompiledStateGraph:
    """The chat graph compiled with ``checkpointer`` (about 2 ms; done per request so no
    compiled graph outlives the saver it is bound to)."""
    return _BUILDER.compile(checkpointer=checkpointer)


def _config(episode_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id_for(episode_id)}}


def _thread_lock(thread_id: str) -> asyncio.Lock:
    lock = _thread_locks.get(thread_id)
    if lock is None:
        lock = asyncio.Lock()
        _thread_locks[thread_id] = lock
    return lock


# --------------------------------------------------------------------------- entry points


async def stream_chat(
    deps: AgentDeps, checkpointer: BaseCheckpointSaver, episode_id: str, message: str
) -> AsyncIterator[AnswerStreamEvent]:
    """Reply to ``message`` in the episode's chat as contract events:
    ``status* claim* (answer | error) done``. Never raises for expected failures.
    On success the user message and the reply are appended to the thread's history."""
    yield StatusEvent(message="Reading the transcript")
    graph = chat_graph(checkpointer)
    run_input: ChatState = {
        "episode_id": episode_id,
        "message": message,
        "received_at": _now_iso(),
        "started": time.perf_counter(),
        "claims": None,
        "llm": None,
        "error": None,
    }
    final: ChatState = run_input
    try:
        async with _thread_lock(thread_id_for(episode_id)):
            async for mode, chunk in graph.astream(
                run_input,
                _config(episode_id),
                context=deps,
                stream_mode=["custom", "values"],
            ):
                if mode == "custom":
                    yield chunk
                else:
                    final = chunk
    except Exception:
        log.exception("chat failed unexpectedly (episode %s)", episode_id)
        yield ErrorEvent(message=INTERNAL_ERROR_MESSAGE, code="internal")
        yield DoneEvent()
        return

    error = final.get("error")
    if error:
        yield ErrorEvent(message=error["message"], code=error["code"])
    else:
        yield AnswerEvent(answer=_answer(final))
    yield DoneEvent()


async def get_chat_history(checkpointer: BaseCheckpointSaver, episode_id: str) -> list[ChatMessage]:
    """The episode's chat, oldest first ([] when there is none)."""
    snapshot = await chat_graph(checkpointer).aget_state(_config(episode_id))
    turns: list[Turn] = snapshot.values.get("turns", []) if snapshot.values else []
    return [
        ChatMessage(
            id=t["id"],
            role=ChatRole(t["role"]),
            text=t["text"],
            claims=[Claim.model_validate(c) for c in t["claims"]],
            created_at=datetime.fromisoformat(t["created_at"]),
        )
        for t in turns
    ]


# --------------------------------------------------------------------------- helpers


def _answer(state: ChatState) -> Answer:
    llm = state.get("llm") or {}
    return Answer(
        claims=[Claim.model_validate(c) for c in state.get("claims") or []],
        model=llm.get("model", ""),
        prompt_version=PROMPT_VERSION,
        tokens_in=llm.get("tokens_in", 0),
        tokens_out=llm.get("tokens_out", 0),
        cost_usd=llm.get("cost_usd", 0.0),
        cached=False,
    )


def _error_dict(error: AgentError) -> dict[str, str]:
    return {"code": error.code, "message": error.message, "detail": error.detail}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


__all__ = [
    "MAX_PRIOR_MESSAGES",
    "PROMPT_VERSION",
    "chat_graph",
    "get_chat_history",
    "stream_chat",
]
