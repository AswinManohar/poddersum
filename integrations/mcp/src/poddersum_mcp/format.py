"""Compact markdown renderings of API objects. Timestamps link to the original audio with a
media fragment, ``[m:ss](audio_url#t=seconds)``, the same links the web app uses."""

from __future__ import annotations

from typing import Any

JSON = dict[str, Any]

SECTION_TITLES = {
    "who_its_for": "Who it's for",
    "whats_new": "What's new",
    "best_10_minutes": "Best 10 minutes",
}
STYLE_TITLES = {"tldr": "TL;DR", "takeaways": "Key takeaways", "verdict": "Worth your time?"}


def timestamp(ms: int) -> str:
    total = max(0, int(ms) // 1000)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def audio_link(audio_url: str, start_ms: int) -> str:
    base = audio_url.split("#", 1)[0]
    return f"{base}#t={max(0, int(start_ms) // 1000)}"


def duration(seconds: int | None) -> str | None:
    if not seconds or seconds <= 0:
        return None
    minutes = max(1, round(seconds / 60))
    h, m = divmod(minutes, 60)
    if not h:
        return f"{m} min"
    return f"{h} h {m} min" if m else f"{h} h"


def citation_links(claim: JSON, audio_url: str | None) -> str:
    seen: set[int] = set()
    parts: list[str] = []
    for c in claim.get("citations") or []:
        start = int(c.get("start_ms") or 0)
        if start // 1000 in seen:
            continue
        seen.add(start // 1000)
        label = timestamp(start)
        parts.append(f"[{label}]({audio_link(audio_url, start)})" if audio_url else f"[{label}]")
    return " ".join(parts)


def claims_markdown(claims: list[JSON], audio_url: str | None) -> str:
    """Bullets with timestamp links; grouped under headings when claims carry ``section``."""
    if not claims:
        return "_No grounded claims were returned for this request._"
    lines: list[str] = []
    current: str | None = None
    has_sections = any(c.get("section") for c in claims)
    for claim in claims:
        section = claim.get("section")
        if has_sections and section != current:
            current = section
            title = SECTION_TITLES.get(section or "", (section or "Other").replace("_", " ").title())
            lines.append(f"\n**{title}**")
        links = citation_links(claim, audio_url)
        lines.append(f"- {claim.get('text', '').strip()} {links}".rstrip())
    return "\n".join(lines).strip()


def episode_heading(ep: JSON) -> str:
    podcast = (ep.get("podcast") or {}).get("title")
    bits = [p for p in (podcast, (ep.get("published_at") or "")[:10], duration(ep.get("duration_s"))) if p]
    meta = " · ".join(bits)
    return f"### {ep.get('title', 'Episode')}\n{meta}" if meta else f"### {ep.get('title', 'Episode')}"


def podcasts_markdown(results: list[JSON], query: str) -> str:
    if not results:
        return f'No podcasts with a public RSS feed matched "{query}".'
    lines = [f'Podcasts matching "{query}":']
    for i, p in enumerate(results, 1):
        extra = " · ".join(x for x in (p.get("author"), p.get("genre")) if x)
        lines.append(f"{i}. **{p['title']}**{f' ({extra})' if extra else ''} - podcast_id `{p['id']}`")
    return "\n".join(lines)


def episode_line(ep: JSON, index: int | None = None) -> str:
    bits = [b for b in ((ep.get("published_at") or "")[:10], duration(ep.get("duration_s"))) if b]
    status = ep.get("status")
    if status and status != "new":
        bits.append(f"transcript: {status}")
    elif ep.get("has_feed_transcript"):
        bits.append("feed transcript")
    prefix = f"{index}. " if index is not None else "- "
    meta = f" ({', '.join(bits)})" if bits else ""
    return f"{prefix}**{ep.get('title', '')}**{meta} - episode_id `{ep['id']}`"


def episodes_markdown(data: JSON) -> str:
    podcast = data.get("podcast") or {}
    eps = data.get("episodes") or []
    total = data.get("total", len(eps))
    head = f"**{podcast.get('title', 'Podcast')}** - {len(eps)} of {total} episodes, newest first:"
    return "\n".join([head, *(episode_line(e, i) for i, e in enumerate(eps, 1))])


def episode_markdown(ep: JSON) -> str:
    lines = [episode_heading(ep), f"episode_id `{ep['id']}` · status: {ep.get('status', 'new')}"]
    if ep.get("active_job_id"):
        lines.append(f"active job: `{ep['active_job_id']}`")
    if ep.get("audio_url"):
        lines.append(f"audio: {ep['audio_url']}")
    if ep.get("description"):
        desc = ep["description"].strip()
        lines.append("\n" + (desc[:600] + "..." if len(desc) > 600 else desc))
    return "\n".join(lines)


def job_markdown(job: JSON) -> str:
    pct = round(float(job.get("progress") or 0) * 100)
    line = f"Job `{job['job_id']}`: {job.get('state')} at step `{job.get('step')}` ({pct}%)"
    if job.get("message"):
        line += f" - {job['message']}"
    if job.get("error"):
        line += f"\nError: {job['error']}"
    return line


def answer_markdown(ep: JSON, answer: JSON, heading: str) -> str:
    claims = claims_markdown(answer.get("claims") or [], ep.get("audio_url"))
    return f"{episode_heading(ep)}\n\n**{heading}**\n{claims}"


def link_base(audio_url: str | None) -> str:
    return f"{(audio_url or '').split('#', 1)[0]}#t="


def segment_line(seg: JSON) -> str:
    """``[segment_id] m:ss (t=seconds) Speaker: text`` - one transcript segment."""
    start = int(seg.get("start_ms") or 0)
    speaker = f"{seg['speaker']}: " if seg.get("speaker") else ""
    text = " ".join(str(seg.get("text") or "").split())
    return f"[{seg.get('id')}] {timestamp(start)} (t={start // 1000}) {speaker}{text}"


CITATION_RULES = (
    "Citation rules: every claim ends with one or more links `[m:ss]({base}<t>)` where m:ss and "
    "t are copied from the start of a segment above that supports the claim (t is that "
    "segment's `t=` value in seconds). Never invent or round timestamps; drop any claim you "
    "cannot tie to a segment. Use only the transcript, not outside knowledge."
)
