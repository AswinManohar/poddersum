"""Ask-anything answer engine: a small LangGraph graph over the stored transcript.

Graph::

    START -> load_context -> generate -> validate_citations -> persist -> END
    (an error in load_context or generate skips straight to persist)

* ``load_context``: reads the episode's segments from Postgres (never audio), builds the
  messages (see ``agent.transcript_context``) and checks they fit the context window.
* ``generate``: streams the model's NDJSON reply through ``Gateway.stream``; each claim
  line is validated with ``agent.citations.ground_claim`` as soon as it is complete and,
  if it survives, sent to the caller as a ``ClaimEvent``.
* ``validate_citations``: the authoritative pass over all claims with the same validator
  (pure and deterministic, so it yields exactly the claims already streamed, in the same
  order) and builds the final ``Answer``; zero surviving claims -> ``no_grounded_claims``.
* ``persist``: writes one ``requests`` row (feature ``ask``) for success and failure,
  with tokens, cached tokens, cost, model, latency and prompt version.

Streaming mechanism: nodes push contract events (``StatusEvent``, ``ClaimEvent``) through
LangGraph's custom stream writer (``runtime.stream_writer``), and :func:`stream_answer`
runs the graph with ``stream_mode=["custom", "values"]``. Custom chunks are forwarded to
the caller the moment a node writes them, while the node is still running; the last
``values`` chunk is the final state, from which the closing ``answer`` or ``error`` event
is made. This needs no queues or callbacks of our own. Dependencies (database sessions,
gateway) travel as the run's ``context`` (``AgentDeps``), not in the state.

Errors never escape as exceptions from expected failures: nodes put an ``AgentError`` in
the state and route to ``persist``. Event order is always ``status* claim* (answer |
error) done``.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator, Iterable, Mapping
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import StreamWriter

from agent import errors
from agent.citations import ModelClaim, ground_claim, ground_claims, segments_by_id
from agent.deps import ANSWER_TASK, AgentDeps
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
    ClaimEvent,
    DoneEvent,
    ErrorEvent,
    StatusEvent,
)
from db import queries
from llm import CallMetadata, LLMError, LLMResult

log = logging.getLogger(__name__)

ASK_PROMPT = load_prompt("ask")
PROMPT_VERSION = answer_version(ASK_PROMPT)
FEATURE = "ask"

INTERNAL_ERROR_MESSAGE = "Something went wrong while answering. Please try again."


class AskState(TypedDict, total=False):
    # input
    episode_id: str
    question: str
    started: float  # time.perf_counter() when the request started (for latency)
    # set by the nodes
    segments: list[SegmentRef]
    messages: list[dict[str, Any]]
    raw_claims: list[ModelClaim]
    llm: LLMResult
    answer: Answer
    error: AgentError


# --------------------------------------------------------------------------- nodes


async def load_context(state: AskState, runtime: Runtime[AgentDeps]) -> AskState:
    deps = runtime.context
    segments = await deps.load_segments(state["episode_id"])
    if not segments:
        return {"error": errors.not_ready()}
    messages = build_messages(segments, ASK_PROMPT.render(question=state["question"]))
    context_window, reserved_output = deps.context_limits()
    try:
        ensure_fits(messages, context_window=context_window, reserved_output=reserved_output)
    except TranscriptTooLongError as exc:
        return {"error": AgentError(errors.TRANSCRIPT_TOO_LONG, str(exc))}
    return {"segments": segments, "messages": messages}


async def generate(state: AskState, runtime: Runtime[AgentDeps]) -> AskState:
    writer = runtime.stream_writer
    writer(StatusEvent(message="Writing the answer"))
    segments = segments_by_id(state["segments"])
    parser = NdjsonClaimParser()
    raw_claims: list[ModelClaim] = []
    streamed = 0  # valid claims sent so far = index of the next ClaimEvent
    result: LLMResult | None = None
    metadata = CallMetadata(
        feature=FEATURE, prompt_version=PROMPT_VERSION, episode_id=state["episode_id"]
    )
    try:
        stream = runtime.context.gateway.stream(ANSWER_TASK, state["messages"], metadata=metadata)
        async for item in stream:
            if isinstance(item, LLMResult):
                result = item
            else:
                new = parser.feed(item)
                streamed = _stream_valid_claims(new, raw_claims, segments, writer, streamed)
        streamed = _stream_valid_claims(parser.close(), raw_claims, segments, writer, streamed)
    except LLMError as exc:
        log.warning("ask generation failed: %s", exc)
        return {"raw_claims": raw_claims, "error": errors.from_llm_error(exc)}
    assert result is not None, "Gateway.stream always ends with an LLMResult"
    return {"raw_claims": raw_claims, "llm": result}


def _stream_valid_claims(
    new: Iterable[ModelClaim],
    raw_claims: list[ModelClaim],
    segments: Mapping[int, SegmentRef],
    writer: StreamWriter,
    streamed: int,
) -> int:
    """Record each newly parsed claim and send the ones that pass validation to the
    caller, numbered from ``streamed``. Returns the new number of claims sent."""
    for raw in new:
        raw_claims.append(raw)
        claim = ground_claim(raw, segments)
        if claim is not None:
            writer(ClaimEvent(index=streamed, claim=claim))
            streamed += 1
    return streamed


def validate_citations(state: AskState) -> AskState:
    claims = ground_claims(state["raw_claims"], segments_by_id(state["segments"]))
    dropped = len(state["raw_claims"]) - len(claims)
    if dropped:
        log.info("ask: dropped %d of %d claims", dropped, len(state["raw_claims"]))
    if not claims:
        return {"error": errors.no_grounded_claims()}
    llm = state["llm"]
    answer = Answer(
        claims=claims,
        model=llm.model,
        prompt_version=PROMPT_VERSION,
        tokens_in=llm.tokens_in,
        tokens_out=llm.tokens_out,
        cost_usd=llm.cost_usd,
        cached=False,
    )
    return {"answer": answer}


async def persist(state: AskState, runtime: Runtime[AgentDeps]) -> AskState:
    llm = state.get("llm")
    answer = state.get("answer")
    error = state.get("error")
    async with runtime.context.sessionmaker() as session:
        await queries.record_request(
            session,
            episode_id=state["episode_id"],
            feature=FEATURE,
            question=state["question"],
            answer=answer.model_dump(mode="json") if answer else None,
            prompt_version=PROMPT_VERSION,
            model=llm.model if llm else None,
            tokens_in=llm.tokens_in if llm else 0,
            tokens_out=llm.tokens_out if llm else 0,
            tokens_cached=llm.tokens_cached if llm else 0,
            cost_usd=llm.cost_usd if llm else 0,
            cached=False,
            latency_ms=int((time.perf_counter() - state["started"]) * 1000),
            error=error.detail if error else None,
        )
        await session.commit()
    return {}


# --------------------------------------------------------------------------- wiring


def _next_or_persist(next_node: str):
    def route(state: AskState) -> str:
        return "persist" if state.get("error") else next_node

    return route


def build_answer_graph():
    return (
        StateGraph(AskState, context_schema=AgentDeps)
        .add_node("load_context", load_context)
        .add_node("generate", generate)
        .add_node("validate_citations", validate_citations)
        .add_node("persist", persist)
        .add_edge(START, "load_context")
        .add_conditional_edges(
            "load_context", _next_or_persist("generate"), ["generate", "persist"]
        )
        .add_conditional_edges(
            "generate", _next_or_persist("validate_citations"), ["validate_citations", "persist"]
        )
        .add_edge("validate_citations", "persist")
        .add_edge("persist", END)
        .compile()
    )


ANSWER_GRAPH = build_answer_graph()


async def stream_answer(
    deps: AgentDeps, episode_id: str, question: str
) -> AsyncIterator[AnswerStreamEvent]:
    """Answer ``question`` about the episode as contract events:
    ``status* claim* (answer | error) done``. Never raises for expected failures."""
    yield StatusEvent(message="Reading the transcript")
    initial: AskState = {
        "episode_id": episode_id,
        "question": question,
        "started": time.perf_counter(),
    }
    final: AskState = initial
    try:
        async for mode, chunk in ANSWER_GRAPH.astream(
            initial, context=deps, stream_mode=["custom", "values"]
        ):
            if mode == "custom":
                yield chunk
            else:
                final = chunk
    except Exception:
        log.exception("ask failed unexpectedly (episode %s)", episode_id)
        yield ErrorEvent(message=INTERNAL_ERROR_MESSAGE, code="internal")
        yield DoneEvent()
        return

    error = final.get("error")
    if error is not None:
        yield ErrorEvent(message=error.message, code=error.code)
    else:
        yield AnswerEvent(answer=final["answer"])
    yield DoneEvent()


__all__ = ["ANSWER_GRAPH", "PROMPT_VERSION", "stream_answer"]
