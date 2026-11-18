"""Preset answers (TL;DR, takeaways, verdict): generated once, then served from cache.

:func:`get_or_create_artifact` is the only entry point.

Cache: the ``artifacts`` table, keyed by (episode, kind, prompt version), where the prompt
version is ``answer_version(<preset prompt>)``, e.g. ``tldr-v1+system-v1``. Bumping a
prompt's version therefore regenerates that preset on its next request; older rows stay.

* Hit: the stored answer is returned with ``answer.cached = True`` and no model call. A
  ``requests`` row is still written (``cached=True``, zero tokens and cost) so cache hit
  rates can be measured. The returned ``Answer`` keeps the tokens and cost of the call
  that originally produced it.
* Miss: same context as ask (system prompt + full transcript, see
  ``agent.transcript_context``) followed by the preset prompt, one ``Gateway.structured``
  call, citation validation, then ``save_artifact`` and a ``requests`` row.

Concurrency: concurrent misses for one key in this process wait on one ``asyncio.Lock``
and re-check the cache, so the model is called once. Across processes ``save_artifact``
is race-safe and returns the stored row, which every caller then serves.

Failures raise :class:`agent.errors.AgentError` (a ``requests`` row with ``error`` is
written first); the route maps them to HTTP errors.
"""

from __future__ import annotations

import asyncio
import logging
import time
import weakref
from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel

from agent import errors
from agent.citations import ModelClaim, ground_claims, segments_by_id
from agent.deps import ANSWER_TASK, AgentDeps
from agent.errors import AgentError
from agent.prompts import Prompt, answer_version, load_prompt
from agent.transcript_context import TranscriptTooLongError, build_messages, ensure_fits
from api.schemas import Answer, Artifact, ArtifactKind, Claim, VerdictSection
from db import queries
from db.models import ArtifactRow
from llm import CallMetadata, LLMError, LLMResult

log = logging.getLogger(__name__)

FEATURE = "preset"


# --------------------------------------------------------------------------- output schemas
# What the model must return (JSON schema enforced by the provider). The citation
# validator still checks every id; these only shape the reply.


class PresetClaim(BaseModel):
    text: str
    segment_ids: list[int]


class PresetOutput(BaseModel):
    claims: list[PresetClaim]


class VerdictClaim(PresetClaim):
    section: VerdictSection


class VerdictOutput(BaseModel):
    claims: list[VerdictClaim]


@dataclass(frozen=True)
class PresetSpec:
    prompt: Prompt
    output_schema: type[PresetOutput] | type[VerdictOutput]
    sections: type[StrEnum] | None = None  # required claim sections, in display order

    @property
    def version(self) -> str:
        return answer_version(self.prompt)


PRESETS: dict[ArtifactKind, PresetSpec] = {
    ArtifactKind.tldr: PresetSpec(load_prompt("tldr"), PresetOutput),
    ArtifactKind.takeaways: PresetSpec(load_prompt("takeaways"), PresetOutput),
    ArtifactKind.verdict: PresetSpec(load_prompt("verdict"), VerdictOutput, VerdictSection),
}


# --------------------------------------------------------------------------- entry point


async def get_or_create_artifact(deps: AgentDeps, episode_id: str, kind: ArtifactKind) -> Artifact:
    """The preset for the episode, from cache or freshly generated (see module docstring)."""
    started = time.perf_counter()
    spec = PRESETS[kind]
    hit = await _serve_cached(deps, episode_id, kind, spec.version, started)
    if hit is not None:
        return hit
    async with _generation_lock(episode_id, kind, spec.version):
        # Another request may have generated it while we waited for the lock.
        hit = await _serve_cached(deps, episode_id, kind, spec.version, started)
        if hit is not None:
            return hit
        return await _generate(deps, episode_id, kind, spec, started)


# --------------------------------------------------------------------------- steps


async def _serve_cached(
    deps: AgentDeps, episode_id: str, kind: ArtifactKind, version: str, started: float
) -> Artifact | None:
    async with deps.sessionmaker() as session:
        row = await queries.get_artifact(session, episode_id, kind.value, version)
        if row is None:
            return None
        artifact = _to_artifact(row, cached=True)
        await queries.record_request(
            session,
            episode_id=episode_id,
            feature=FEATURE,
            kind=kind.value,
            answer=artifact.answer.model_dump(mode="json"),
            prompt_version=version,
            model=row.model,
            cached=True,
            latency_ms=_elapsed_ms(started),
        )
        await session.commit()
    return artifact


async def _generate(
    deps: AgentDeps, episode_id: str, kind: ArtifactKind, spec: PresetSpec, started: float
) -> Artifact:
    llm: LLMResult | None = None
    try:
        segments = await deps.load_segments(episode_id)
        if not segments:
            raise errors.not_ready()
        messages = build_messages(segments, spec.prompt.text)
        context_window, reserved_output = deps.context_limits()
        try:
            ensure_fits(messages, context_window=context_window, reserved_output=reserved_output)
        except TranscriptTooLongError as exc:
            raise AgentError(errors.TRANSCRIPT_TOO_LONG, str(exc)) from None

        metadata = CallMetadata(
            feature=f"preset:{kind.value}", prompt_version=spec.version, episode_id=episode_id
        )
        try:
            parsed, llm = await deps.gateway.structured(
                ANSWER_TASK, messages, spec.output_schema, metadata=metadata
            )
        except LLMError as exc:
            log.warning("preset %s generation failed: %s", kind.value, exc)
            raise errors.from_llm_error(exc) from None

        raw = [ModelClaim.model_validate(c.model_dump(mode="json")) for c in parsed.claims]
        claims = _in_section_order(
            ground_claims(raw, segments_by_id(segments), spec.sections), spec.sections
        )
        if not claims:
            raise errors.no_grounded_claims()
    except AgentError as exc:
        await _record_failure(deps, episode_id, kind, spec.version, llm, exc, started)
        raise

    assert llm is not None
    answer = Answer(
        claims=claims,
        model=llm.model,
        prompt_version=spec.version,
        tokens_in=llm.tokens_in,
        tokens_out=llm.tokens_out,
        cost_usd=llm.cost_usd,
        cached=False,
    )
    async with deps.sessionmaker() as session:
        row = await queries.save_artifact(
            session,
            episode_id=episode_id,
            kind=kind.value,
            prompt_version=spec.version,
            answer=answer.model_dump(mode="json"),
            model=llm.model,
            tokens_in=llm.tokens_in,
            tokens_out=llm.tokens_out,
            cost_usd=llm.cost_usd,
        )
        # The stored row may be another process's answer: serve and record that one.
        await queries.record_request(
            session,
            episode_id=episode_id,
            feature=FEATURE,
            kind=kind.value,
            answer=row.answer,
            prompt_version=spec.version,
            model=llm.model,
            tokens_in=llm.tokens_in,
            tokens_out=llm.tokens_out,
            tokens_cached=llm.tokens_cached,
            cost_usd=llm.cost_usd,
            latency_ms=_elapsed_ms(started),
        )
        await session.commit()
    return _to_artifact(row, cached=False)


async def _record_failure(
    deps: AgentDeps,
    episode_id: str,
    kind: ArtifactKind,
    version: str,
    llm: LLMResult | None,
    error: AgentError,
    started: float,
) -> None:
    """A ``requests`` row for a failed generation; tokens and cost are recorded when a
    model call had completed (e.g. every claim was dropped by the validator)."""
    async with deps.sessionmaker() as session:
        await queries.record_request(
            session,
            episode_id=episode_id,
            feature=FEATURE,
            kind=kind.value,
            answer=None,
            prompt_version=version,
            model=llm.model if llm else None,
            tokens_in=llm.tokens_in if llm else 0,
            tokens_out=llm.tokens_out if llm else 0,
            tokens_cached=llm.tokens_cached if llm else 0,
            cost_usd=llm.cost_usd if llm else 0,
            latency_ms=_elapsed_ms(started),
            error=error.detail,
        )
        await session.commit()


# --------------------------------------------------------------------------- helpers

_locks: weakref.WeakValueDictionary[tuple[str, str, str], asyncio.Lock] = (
    weakref.WeakValueDictionary()
)
"""One lock per cache key while someone holds or waits on it (freed when unused)."""


def _generation_lock(episode_id: str, kind: ArtifactKind, version: str) -> asyncio.Lock:
    key = (episode_id, kind.value, version)
    lock = _locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _locks[key] = lock
    return lock


def _in_section_order(claims: list[Claim], sections: type[StrEnum] | None) -> list[Claim]:
    """Verdict claims grouped by section in display order (stable within a section)."""
    if sections is None:
        return claims
    order = {s.value: i for i, s in enumerate(sections)}
    return sorted(claims, key=lambda c: order[c.section or ""])


def _to_artifact(row: ArtifactRow, *, cached: bool) -> Artifact:
    answer = Answer.model_validate(row.answer).model_copy(update={"cached": cached})
    return Artifact(
        episode_id=row.episode_id,
        kind=ArtifactKind(row.kind),
        prompt_version=row.prompt_version,
        answer=answer,
        created_at=row.created_at,
    )


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
