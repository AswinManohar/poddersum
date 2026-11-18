"""High-level PodderSum operations used by the MCP tools.

* :func:`resolve_episode` turns an id, a show name + hint, or a pasted link into an episode.
* :func:`ensure_ready` starts (or joins) ingestion and follows the job's SSE stream until the
  transcript is ready or ``wait_s`` runs out. Ingest is idempotent per episode, so calling
  again after a timeout simply rejoins the same job.
* :func:`summarize`, :func:`ask`, :func:`chat` produce the final, cited answer.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from poddersum_mcp import format as fmt
from poddersum_mcp.client import (
    JSON,
    PodderSumClient,
    PodderSumError,
    collect_answer,
    is_model_unavailable,
)
from poddersum_mcp.resolve import is_episode_id, parse_link, pick_episode, pick_podcast

Style = Literal["tldr", "takeaways", "verdict"]
Engine = Literal["auto", "host", "server"]
ENGINES: tuple[str, ...] = ("auto", "host", "server")
# Characters of transcript returned per call (a 1 h episode is roughly 50-60k characters).
DEFAULT_TRANSCRIPT_CHARS = 60_000
MAX_TRANSCRIPT_CHARS = 200_000
TRANSCRIPT_PAGE_SEGMENTS = 500
HOST_TASKS = {
    "tldr": "Write a short TL;DR of this episode: the 3 to 6 most important points, as bullets.",
    "takeaways": "List the key, actionable takeaways from this episode, as bullets.",
    "verdict": (
        "Say whether this episode is worth listening to, under three headings: Who it's for, "
        "What's new, Best 10 minutes (name the stretch and link its start)."
    ),
}
STYLES: tuple[str, ...] = ("tldr", "takeaways", "verdict")
ProgressFn = Callable[[float, str], Awaitable[None]]
EPISODES_FOR_MATCHING = 100

STYLE_QUESTIONS = {
    "tldr": "Give a short TL;DR of this episode: the 3 to 6 most important points.",
    "takeaways": "List the key, actionable takeaways from this episode.",
    "verdict": (
        "Is this episode worth listening to? Say who it is for, what is new or surprising in "
        "it, and which stretch is the best 10 minutes."
    ),
}


async def _no_progress(_p: float, _m: str) -> None:
    return None


@dataclass
class Resolution:
    episode: JSON | None = None
    podcast: JSON | None = None
    candidates: list[JSON] = field(default_factory=list)
    note: str = ""


def episode_summary(ep: JSON) -> JSON:
    """The compact episode object used in structured tool output."""
    return {
        "id": ep["id"],
        "title": ep.get("title"),
        "podcast_id": ep.get("podcast_id"),
        "podcast_title": (ep.get("podcast") or {}).get("title"),
        "published_at": ep.get("published_at"),
        "duration_s": ep.get("duration_s"),
        "status": ep.get("status"),
        "audio_url": ep.get("audio_url"),
        "has_feed_transcript": ep.get("has_feed_transcript"),
    }


def linked_answer(answer: JSON, audio_url: str | None) -> JSON:
    """Answer dict with ``timestamp`` and ``url`` added to every citation."""
    claims = []
    for claim in answer.get("claims") or []:
        cites = []
        for c in claim.get("citations") or []:
            start = int(c.get("start_ms") or 0)
            extra = {"timestamp": fmt.timestamp(start)}
            if audio_url:
                extra["url"] = fmt.audio_link(audio_url, start)
            cites.append({**c, **extra})
        claims.append({**claim, "citations": cites})
    keep = ("model", "prompt_version", "tokens_in", "tokens_out", "cost_usd", "cached")
    return {"claims": claims, **{k: answer[k] for k in keep if k in answer}}


def build_question(style: str, angle: str | None) -> str:
    base = STYLE_QUESTIONS[style]
    if not angle or not angle.strip():
        return base
    return f"{base} Answer from this angle, and only cover what is relevant to it: {angle.strip()}"


async def resolve_episode(
    client: PodderSumClient,
    *,
    episode_id: str | None = None,
    podcast_name: str | None = None,
    episode_hint: str | None = None,
) -> Resolution:
    if is_episode_id(episode_id) or is_episode_id(episode_hint):
        eid = (episode_id if is_episode_id(episode_id) else episode_hint).strip()  # type: ignore[union-attr]
        try:
            ep = await client.get_episode(eid)
        except PodderSumError as exc:
            if exc.status == 404:
                raise PodderSumError(
                    f"Unknown episode_id `{eid}`. Episode ids become known after "
                    "search_podcasts + list_episodes; pass podcast_name and episode_hint instead.",
                    code="not_found",
                    status=404,
                ) from exc
            raise
        return Resolution(episode=ep, podcast=ep.get("podcast"), note="by episode_id")
    if episode_id:
        raise PodderSumError(
            "episode_id must be the 64-character hex id returned by list_episodes.",
            code="invalid_request",
        )

    link = parse_link(podcast_name) or parse_link(episode_hint)
    query = (podcast_name or "").strip()
    itunes_id = None
    if link is not None:
        if link.kind == "apple":
            itunes_id = link.itunes_id
            query = link.show_query or ("" if query.startswith("http") else query)
            if episode_hint and link.url in episode_hint:
                episode_hint = None
        elif link.kind == "spotify":
            raise PodderSumError(
                "Spotify links cannot be resolved by PodderSum yet. Ask the user for the show "
                "name and the episode title (or open the link to read them), then call again "
                "with podcast_name and episode_hint.",
                code="unsupported_link",
            )
        else:
            raise PodderSumError(
                "PodderSum cannot look up a podcast by feed or web URL yet; it searches by show "
                "name. Pass the show's name as podcast_name (read it from the page or feed "
                "title if needed).",
                code="unsupported_link",
            )
    if not query:
        raise PodderSumError(
            "Give either episode_id or podcast_name (plus an optional episode_hint).",
            code="invalid_request",
        )

    found = await client.search(query, limit=5)
    podcast = pick_podcast(found.get("results") or [], query, itunes_id)
    if podcast is None:
        raise PodderSumError(
            f'No podcast with a public RSS feed matched "{query}". Check the spelling or try '
            "the exact show title.",
            code="not_found",
        )
    listing = await client.list_episodes(podcast["id"], limit=EPISODES_FOR_MATCHING)
    hint = episode_hint
    if link is not None and link.apple_episode_id and not hint:
        match = pick_episode(listing.get("episodes") or [], None)
        match.candidates = (listing.get("episodes") or [])[:5]
        match.episode = None
        match.reason = (
            "the Apple link points at one specific episode, which PodderSum cannot map yet; "
            "confirm which episode"
        )
    else:
        match = pick_episode(listing.get("episodes") or [], hint)
    if match.episode is None:
        return Resolution(
            podcast=listing.get("podcast") or podcast, candidates=match.candidates, note=match.reason
        )
    ep = await client.get_episode(match.episode["id"])
    return Resolution(episode=ep, podcast=ep.get("podcast") or podcast, note=match.reason)


@dataclass
class ReadyState:
    ready: bool
    job: JSON | None = None


async def ensure_ready(
    client: PodderSumClient, ep: JSON, *, wait_s: float, progress: ProgressFn = _no_progress
) -> ReadyState:
    """Make sure the episode has a transcript, waiting at most ``wait_s`` seconds."""
    if ep.get("status") == "ready":
        return ReadyState(True)
    job = await client.start_ingest(ep["id"])
    if job.get("state") == "succeeded":
        return ReadyState(True, job)
    await progress(float(job.get("progress") or 0.0), _job_message(job))
    try:
        async with asyncio.timeout(wait_s):
            async for event in client.job_events(job["job_id"]):
                await progress(float(event.get("progress") or 0.0), _job_message(event))
                if event.get("state") == "succeeded":
                    return ReadyState(True, {**job, **event})
                if event.get("state") == "failed":
                    final = await client.get_job(job["job_id"])
                    reason = final.get("error") or event.get("message") or "unknown error"
                    raise PodderSumError(
                        f"Transcribing the episode failed: {reason}. Calling again retries.",
                        code="ingest_failed",
                    )
    except TimeoutError:
        return ReadyState(False, await client.get_job(job["job_id"]))
    # Stream closed without a terminal event: trust the job row.
    final = await client.get_job(job["job_id"])
    return ReadyState(final.get("state") == "succeeded", final)


def _job_message(job: JSON) -> str:
    step = str(job.get("step") or "").replace("_", " ")
    msg = job.get("message")
    return f"{step}: {msg}" if msg else step or str(job.get("state") or "")


def processing_result(ep: JSON, job: JSON | None) -> tuple[str, JSON]:
    job = job or {}
    text = (
        f"{fmt.episode_heading(ep)}\n\nStill transcribing this episode "
        f"({round(float(job.get('progress') or 0) * 100)}%, step `{job.get('step')}`). "
        f"Job id `{job.get('job_id')}`. Call the same tool again with episode_id "
        f"`{ep['id']}` in a minute to continue (it rejoins the job), or check get_job_status."
    )
    return text, {"status": "processing", "episode": episode_summary(ep), "job": job}


def choice_result(res: Resolution) -> tuple[str, JSON]:
    podcast = res.podcast or {}
    lines = [
        f"Matched podcast **{podcast.get('title', '?')}**, but {res.note}. Ask the user which "
        "episode they mean, then call again with its episode_id:",
        *(fmt.episode_line(c, i) for i, c in enumerate(res.candidates, 1)),
    ]
    data = {
        "status": "needs_choice",
        "reason": res.note,
        "podcast": {k: podcast.get(k) for k in ("id", "title", "author")},
        "candidates": [
            {k: c.get(k) for k in ("id", "title", "published_at", "duration_s", "status")}
            for c in res.candidates
        ],
    }
    return "\n".join(lines), data


async def answer_for(
    client: PodderSumClient,
    ep: JSON,
    *,
    style: str,
    angle: str | None,
    progress: ProgressFn = _no_progress,
) -> tuple[JSON, str]:
    """(answer dict, source) where source is ``artifact`` (cached preset) or ``ask``."""
    if angle and angle.strip():
        await progress(1.0, "answering from the requested angle")

        async def on_status(message: str) -> None:
            await progress(1.0, message)

        answer = await collect_answer(client.ask(ep["id"], build_question(style, angle)), on_status)
        return answer, "ask"
    await progress(1.0, f"loading the {style} summary")
    artifact = await client.get_artifact(ep["id"], style)
    return artifact["answer"], "artifact"


async def fetch_transcript(
    client: PodderSumClient,
    ep: JSON,
    *,
    start_ms: int | None = None,
    end_ms: int | None = None,
    max_chars: int = DEFAULT_TRANSCRIPT_CHARS,
) -> JSON:
    """Segments from ``start_ms`` until ``max_chars`` of formatted lines (or ``end_ms``)."""
    max_chars = max(1_000, min(int(max_chars), MAX_TRANSCRIPT_CHARS))
    segments: list[JSON] = []
    used = 0
    cursor = start_ms
    total = None
    next_start: int | None = None
    while True:
        page = await client.get_transcript(
            ep["id"], start_ms=cursor, end_ms=end_ms, limit=TRANSCRIPT_PAGE_SEGMENTS
        )
        total = page.get("total_segments", total)
        page_segments = page.get("segments") or []
        for seg in page_segments:
            line_len = len(fmt.segment_line(seg)) + 1
            if segments and used + line_len > max_chars:
                next_start = int(seg["start_ms"])
                break
            segments.append(seg)
            used += line_len
        else:
            if page.get("next_start_ms") is not None and page_segments:
                cursor = int(page["next_start_ms"])
                continue
            next_start = None
        break
    return {
        "episode_id": ep["id"],
        "audio_url": ep.get("audio_url"),
        "link_base": fmt.link_base(ep.get("audio_url")),
        "total_segments": total,
        "segments": segments,
        "next_start_ms": next_start,
        "chars": used,
    }


def transcript_result(ep: JSON, page: JSON, *, task: str, status: str, reason: str) -> tuple[str, JSON]:
    """Transcript text for the host model to work from, with the task and citation rules."""
    segs = page["segments"]
    span = "empty"
    if segs:
        last = segs[-1].get("end_ms") or segs[-1]["start_ms"]
        span = f"{fmt.timestamp(segs[0]['start_ms'])} - {fmt.timestamp(last)}"
    total = page.get("total_segments")
    count = f"{len(segs)} of {total}" if total is not None else str(len(segs))
    lines = [
        fmt.episode_heading(ep),
        f"episode_id `{ep['id']}` · audio: {ep.get('audio_url')}",
        "",
        f"**{reason}** Task for you (the host model): {task}",
        fmt.CITATION_RULES.format(base=page["link_base"]),
        "",
        f"Transcript segments ({count}, {span}): `[segment_id] m:ss (t=seconds) Speaker: text`",
        *(fmt.segment_line(seg) for seg in segs),
    ]
    if page.get("next_start_ms") is not None:
        lines += [
            "",
            f'More transcript follows: call get_transcript(episode_id="{ep["id"]}", '
            f"start_ms={page['next_start_ms']}) and continue before answering.",
        ]
    data = {
        "status": status,
        "reason": reason,
        "task": task,
        "episode": episode_summary(ep),
        "transcript": page,
    }
    return "\n".join(lines), data


def host_summary_task(style: str, angle: str | None) -> str:
    task = HOST_TASKS[style]
    if angle and angle.strip():
        task += f" Angle: {angle.strip()} - cover only what is relevant to it."
    return task


async def _ready_episode(
    client: PodderSumClient,
    res: Resolution,
    *,
    wait_s: float,
    progress: ProgressFn,
) -> tuple[JSON | None, tuple[str, JSON] | None]:
    """(episode, None) when the transcript is ready, else (None, result to return)."""
    if res.episode is None:
        return None, choice_result(res)
    state = await ensure_ready(client, res.episode, wait_s=wait_s, progress=progress)
    if not state.ready:
        return None, processing_result(res.episode, state.job)
    return res.episode, None


async def _host_fallback(
    client: PodderSumClient, ep: JSON, *, task: str, reason: str, status: str, server_error: str | None
) -> tuple[str, JSON]:
    try:
        page = await fetch_transcript(client, ep)
    except PodderSumError as exc:
        if exc.code == "api_gap" and server_error:
            raise PodderSumError(
                f"PodderSum's server-side model is unavailable ({server_error}) and {exc.message}",
                code="no_engine",
            ) from exc
        raise
    return transcript_result(ep, page, task=task, status=status, reason=reason)


async def summarize(
    client: PodderSumClient,
    *,
    episode_id: str | None,
    podcast_name: str | None,
    episode_hint: str | None,
    style: str,
    angle: str | None,
    wait_s: float,
    engine: str = "auto",
    progress: ProgressFn = _no_progress,
) -> tuple[str, JSON]:
    if style not in STYLES:
        raise PodderSumError(f"style must be one of {', '.join(STYLES)}.", code="invalid_request")
    if engine not in ENGINES:
        raise PodderSumError(f"engine must be one of {', '.join(ENGINES)}.", code="invalid_request")
    res = await resolve_episode(
        client, episode_id=episode_id, podcast_name=podcast_name, episode_hint=episode_hint
    )
    ep, early = await _ready_episode(client, res, wait_s=wait_s, progress=progress)
    if early is not None:
        return early
    assert ep is not None
    task = host_summary_task(style, angle)
    if engine == "host":
        return await _host_fallback(
            client, ep, task=task, status="transcript", server_error=None,
            reason="Host-model mode: write the summary yourself from the transcript below.",
        )  # fmt: skip
    try:
        answer, source = await answer_for(client, ep, style=style, angle=angle, progress=progress)
    except PodderSumError as exc:
        if not is_model_unavailable(exc):
            raise
        if engine == "server":
            return model_unavailable_result(ep, exc, "summary")
        await progress(1.0, "server model unavailable; returning the transcript for the host model")
        return await _host_fallback(
            client, ep, task=task, status="transcript", server_error=f"{exc.code}: {exc.message}",
            reason=f"PodderSum's server-side model is unavailable ({exc.code}), so write the "
            "summary yourself from the transcript below.",
        )  # fmt: skip
    heading = fmt.STYLE_TITLES[style] + (f" (angle: {angle.strip()})" if angle and angle.strip() else "")
    text = fmt.answer_markdown(ep, answer, heading)
    text += f"\n\nepisode_id `{ep['id']}` (use it for ask_episode / chat_episode follow-ups)"
    data = {
        "status": "ok",
        "style": style,
        "angle": angle or None,
        "source": source,
        "matched_by": res.note,
        "episode": episode_summary(ep),
        "answer": linked_answer(answer, ep.get("audio_url")),
    }
    return text, data


def model_unavailable_result(ep: JSON, exc: PodderSumError, what: str) -> tuple[str, JSON]:
    hint = (
        f"PodderSum's server-side model is unavailable ({exc.code}: {exc.message}). Fall back to "
        f'the host model: call get_transcript(episode_id="{ep["id"]}") and write the {what} '
        "yourself, ending every claim with a [m:ss](audio_url#t=seconds) link taken from the "
        "segments (never invented)."
    )
    data = {
        "status": "model_unavailable",
        "code": exc.code,
        "hint": hint,
        "fallback": {"tool": "get_transcript", "episode_id": ep["id"]},
        "episode": episode_summary(ep),
    }
    return f"{fmt.episode_heading(ep)}\n\n{hint}", data


async def converse(
    client: PodderSumClient,
    episode_id: str,
    text: str,
    *,
    mode: Literal["ask", "chat"],
    wait_s: float,
    engine: str = "auto",
    progress: ProgressFn = _no_progress,
) -> tuple[str, JSON]:
    if not text or not text.strip():
        raise PodderSumError("The question/message must not be empty.", code="invalid_request")
    res = await resolve_episode(client, episode_id=episode_id)
    ep, early = await _ready_episode(client, res, wait_s=wait_s, progress=progress)
    if early is not None:
        return early
    assert ep is not None
    task = f"Answer this question from the transcript: {text.strip()}"
    if engine == "host":
        return await _host_fallback(
            client, ep, task=task, status="transcript", server_error=None,
            reason="Host-model mode: answer yourself from the transcript below.",
        )  # fmt: skip

    async def on_status(message: str) -> None:
        await progress(1.0, message)

    stream = client.ask(ep["id"], text) if mode == "ask" else client.chat(ep["id"], text)
    try:
        answer = await collect_answer(stream, on_status)
    except PodderSumError as exc:
        if not is_model_unavailable(exc):
            raise
        if engine == "server":
            return model_unavailable_result(ep, exc, "answer")
        return await _host_fallback(
            client, ep, task=task, status="transcript", server_error=f"{exc.code}: {exc.message}",
            reason=f"PodderSum's server-side model is unavailable ({exc.code}), so answer "
            "yourself from the transcript below.",
        )  # fmt: skip
    heading = f"Q: {text.strip()}" if mode == "ask" else f"You: {text.strip()}"
    out = fmt.answer_markdown(ep, answer, heading)
    data: dict[str, Any] = {
        "status": "ok",
        "mode": mode,
        "episode": episode_summary(ep),
        "answer": linked_answer(answer, ep.get("audio_url")),
    }
    return out, data


async def get_transcript(
    client: PodderSumClient,
    *,
    episode_id: str | None,
    podcast_name: str | None = None,
    episode_hint: str | None = None,
    start_ms: int | None = None,
    end_ms: int | None = None,
    max_chars: int = DEFAULT_TRANSCRIPT_CHARS,
    wait_s: float,
    progress: ProgressFn = _no_progress,
) -> tuple[str, JSON]:
    res = await resolve_episode(
        client, episode_id=episode_id, podcast_name=podcast_name, episode_hint=episode_hint
    )
    ep, early = await _ready_episode(client, res, wait_s=wait_s, progress=progress)
    if early is not None:
        return early
    assert ep is not None
    page = await fetch_transcript(client, ep, start_ms=start_ms, end_ms=end_ms, max_chars=max_chars)
    return transcript_result(
        ep, page, status="transcript",
        task="Use these segments to summarize or answer what the user asked.",
        reason="Transcript for the host model.",
    )  # fmt: skip
