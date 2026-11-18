#!/usr/bin/env python3
"""PodderSum CLI (Python 3.9+, standard library only).

Same HTTP API as the PodderSum MCP server; for agents that cannot use MCP.

  poddersum.py search "hidden brain"
  poddersum.py episodes <podcast_id> [--limit 10]
  poddersum.py episode <episode_id>
  poddersum.py summarize --podcast "Hidden Brain" [--episode latest]
                         [--style tldr|takeaways|verdict] [--angle "for a founder"]
  poddersum.py summarize --episode-id <episode_id> --style verdict
  poddersum.py ask <episode_id> "What did they say about sleep?"
  poddersum.py chat <episode_id> "And the second guest?"
  poddersum.py transcript --episode-id <episode_id> [--start-ms 0] [--end-ms N] [--max-chars 60000]
  poddersum.py summarize --podcast "Hidden Brain" --engine host   # you (the agent) write it
  poddersum.py job <job_id>

No API key is needed. --engine auto (default) uses PodderSum's server-side model when it has
one and otherwise prints the transcript for the calling agent to summarize; --engine host
always prints the transcript; --engine server never falls back.

Config: PODDERSUM_API_URL (default http://localhost:8010), PODDERSUM_API_KEY (optional,
sent as a Bearer token), PODDERSUM_WAIT_S (max seconds to wait for transcription, 240).
Add --json for machine-readable output. Exit codes: 0 ok, 1 error, 2 the episode is
ambiguous (candidates printed; ask the user), 3 still transcribing (run the same command
again later).
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

VERSION = "0.1.0"
DEFAULT_API_URL = "http://localhost:8010"
START_HINT = "cd backend && uv run uvicorn api.main:app --port 8010"
STYLES = ("tldr", "takeaways", "verdict")
STYLE_TITLES = {"tldr": "TL;DR", "takeaways": "Key takeaways", "verdict": "Worth your time?"}
SECTION_TITLES = {
    "who_its_for": "Who it's for",
    "whats_new": "What's new",
    "best_10_minutes": "Best 10 minutes",
}
STYLE_QUESTIONS = {
    "tldr": "Give a short TL;DR of this episode: the 3 to 6 most important points.",
    "takeaways": "List the key, actionable takeaways from this episode.",
    "verdict": (
        "Is this episode worth listening to? Say who it is for, what is new or surprising in "
        "it, and which stretch is the best 10 minutes."
    ),
}
LATEST = re.compile(r"^(the\s+)?(latest|newest|most\s+recent|last|recent|new|current)(\s+(one|episode|ep))?$")
EP_NUMBER = re.compile(r"(?:#|\bep(?:isode)?\.?\s*|\bno\.?\s*)(\d{1,5})\b", re.I)
STOP = set(
    "the a an of and or to in on for with about episode ep podcast show is it this that from at by my "
    "me one where when they he she what which was were talk talks talked discuss discussed said says "
    "new recent".split()
)
SHA256 = re.compile(r"^[0-9a-f]{64}$")
EXIT_OK, EXIT_ERROR, EXIT_CHOICE, EXIT_PROCESSING = 0, 1, 2, 3
DEFAULT_TRANSCRIPT_CHARS = 60000
ENGINES = ("auto", "host", "server")
HOST_TASKS = {
    "tldr": "Write a short TL;DR of this episode: the 3 to 6 most important points, as bullets.",
    "takeaways": "List the key, actionable takeaways from this episode, as bullets.",
    "verdict": (
        "Say whether this episode is worth listening to, under three headings: Who it's for, "
        "What's new, Best 10 minutes (name the stretch and link its start)."
    ),
}
CITATION_RULES = (
    "Citation rules: every claim ends with one or more links `[m:ss](%s<t>)` where m:ss and t "
    "are copied from the start of a segment above that supports the claim (t is that "
    "segment's `t=` value in seconds). Never invent or round timestamps; drop any claim you "
    "cannot tie to a segment. Use only the transcript, not outside knowledge."
)


class CliError(Exception):
    def __init__(self, message, code="error", status=None):
        super().__init__(message)
        self.message, self.code, self.status = message, code, status


# --- HTTP -------------------------------------------------------------------------


class Api:
    def __init__(self, base_url, api_key=None):
        self.base = base_url.rstrip("/")
        self.headers = {"User-Agent": "poddersum-cli/" + VERSION, "Accept": "application/json"}
        if api_key:
            self.headers["Authorization"] = "Bearer " + api_key

    def _open(self, method, path, params=None, body=None, accept=None, timeout=30):
        url = self.base + path + ("?" + urllib.parse.urlencode(params) if params else "")
        headers = dict(self.headers)
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        if accept:
            headers["Accept"] = accept
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            return urllib.request.urlopen(req, timeout=timeout)  # noqa: S310 - user-configured URL
        except urllib.error.HTTPError as exc:
            detail, code = "HTTP %d" % exc.code, "http_error"
            try:
                payload = json.loads(exc.read().decode() or "null")
                if isinstance(payload, dict) and isinstance(payload.get("detail"), str):
                    detail, code = payload["detail"], payload.get("code") or code
            except ValueError:
                pass
            raise CliError(detail, code, exc.code) from None
        except (urllib.error.URLError, ConnectionError, TimeoutError) as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, TimeoutError) or "timed out" in str(reason):
                raise CliError(
                    "PodderSum API at %s timed out on %s %s." % (self.base, method, path), "timeout"
                ) from None
            raise CliError(
                "PodderSum API not reachable at %s; start it with `%s` or set PODDERSUM_API_URL."
                % (self.base, START_HINT),
                "api_unreachable",
            ) from None

    def get(self, path, params=None, timeout=30):
        with self._open("GET", path, params=params, timeout=timeout) as resp:
            return json.loads(resp.read().decode())

    def post(self, path, body=None):
        with self._open("POST", path, body=body) as resp:
            return json.loads(resp.read().decode())

    def stream(self, method, path, body=None, timeout=120):
        """Yield (event, data-dict) per SSE frame, and (":", None) per keep-alive comment."""
        resp = self._open(method, path, body=body, accept="text/event-stream", timeout=timeout)
        event, data = "message", []
        with resp:
            for raw in resp:
                line = raw.decode("utf-8").rstrip("\r\n")
                if not line:
                    if data:
                        yield event, json.loads("\n".join(data))
                    event, data = "message", []
                elif line.startswith(":"):
                    yield ":", None
                else:
                    field, _, value = line.partition(":")
                    value = value[1:] if value.startswith(" ") else value
                    if field == "event":
                        event = value
                    elif field == "data":
                        data.append(value)
            if data:
                yield event, json.loads("\n".join(data))


# --- formatting ---------------------------------------------------------------------


def timestamp(ms):
    total = max(0, int(ms) // 1000)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return "%d:%02d:%02d" % (h, m, s) if h else "%d:%02d" % (m, s)


def audio_link(audio_url, ms):
    return "%s#t=%d" % (audio_url.split("#", 1)[0], max(0, int(ms) // 1000))


def duration(seconds):
    if not seconds or seconds <= 0:
        return None
    h, m = divmod(max(1, round(seconds / 60)), 60)
    return ("%d h %d min" % (h, m) if m else "%d h" % h) if h else "%d min" % m


def claims_md(claims, audio_url):
    if not claims:
        return "_No grounded claims were returned for this request._"
    out, current = [], None
    sections = any(c.get("section") for c in claims)
    for c in claims:
        if sections and c.get("section") != current:
            current = c.get("section")
            out.append(
                "\n**%s**" % SECTION_TITLES.get(current or "", (current or "Other").replace("_", " ").title())
            )
        seen, links = set(), []
        for cit in c.get("citations") or []:
            start = int(cit.get("start_ms") or 0)
            if start // 1000 in seen:
                continue
            seen.add(start // 1000)
            label = timestamp(start)
            links.append("[%s](%s)" % (label, audio_link(audio_url, start)) if audio_url else "[%s]" % label)
        out.append(("- %s %s" % (c.get("text", "").strip(), " ".join(links))).rstrip())
    return "\n".join(out).strip()


def heading(ep):
    bits = [
        b
        for b in (
            (ep.get("podcast") or {}).get("title"),
            (ep.get("published_at") or "")[:10],
            duration(ep.get("duration_s")),
        )
        if b
    ]
    return "### %s%s" % (ep.get("title", "Episode"), "\n" + " · ".join(bits) if bits else "")


def episode_line(ep, i):
    bits = [b for b in ((ep.get("published_at") or "")[:10], duration(ep.get("duration_s"))) if b]
    if ep.get("status") and ep.get("status") != "new":
        bits.append("transcript: " + ep["status"])
    return "%d. **%s**%s - episode_id `%s`" % (
        i,
        ep.get("title", ""),
        " (%s)" % ", ".join(bits) if bits else "",
        ep["id"],
    )


def linked(answer, audio_url):
    claims = []
    for c in answer.get("claims") or []:
        cites = []
        for cit in c.get("citations") or []:
            extra = {"timestamp": timestamp(cit.get("start_ms") or 0)}
            if audio_url:
                extra["url"] = audio_link(audio_url, cit.get("start_ms") or 0)
            cites.append(dict(cit, **extra))
        claims.append(dict(c, citations=cites))
    return dict(answer, claims=claims)


# --- resolution ---------------------------------------------------------------------


def _norm(text):
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _tokens(text):
    return {t for t in _norm(text).split() if t not in STOP and (len(t) > 1 or t.isdigit())}


def parse_link(text):
    m = re.search(r"https?://\S+", text or "")
    if not m:
        return None
    url = m.group(0).rstrip(").,>")
    p = urllib.parse.urlparse(url)
    host = p.netloc.lower()
    if host.endswith("apple.com"):
        parts = [x for x in p.path.split("/") if x]
        slug = next(
            (parts[i + 1] for i, x in enumerate(parts) if x == "podcast" and i + 1 < len(parts)), None
        )
        if slug and slug.startswith("id"):
            slug = None
        itunes = next((int(x[2:]) for x in parts if re.fullmatch(r"id\d+", x)), None)
        episode = urllib.parse.parse_qs(p.query).get("i", [None])[0]
        return {
            "kind": "apple",
            "show": urllib.parse.unquote(slug).replace("-", " ") if slug else None,
            "itunes_id": itunes,
            "episode": episode,
            "url": url,
        }
    if "spotify" in host:
        return {"kind": "spotify", "url": url}
    return {"kind": "other", "url": url}


def pick_episode(episodes, hint):
    """Return (episode, candidates, reason)."""
    if not episodes:
        return None, [], "the podcast has no episodes with audio"
    text = (hint or "").strip()
    if not text or LATEST.match(_norm(text)):
        return episodes[0], [], "newest episode"
    m = EP_NUMBER.search(text)
    if text.isdigit() or m:
        num = text if text.isdigit() else m.group(1)
        pat = re.compile(r"(?:#|\bep(?:isode)?\.?\s*|^|\b)%s\b" % num, re.I)
        hits = [e for e in episodes if pat.search(e.get("title") or "")]
        if len(hits) == 1:
            return hits[0], [], "episode number " + num
        if hits:
            return None, hits[:5], "several episodes match number " + num

    exact = [e for e in episodes if _norm(e.get("title")) == _norm(text)]
    if exact:
        return exact[0], [], "exact title"

    def score(e):
        ratio = difflib.SequenceMatcher(None, _norm(text), _norm(e.get("title"))).ratio()
        ht = _tokens(text)
        if not ht:
            return ratio
        return max(
            ratio,
            len(ht & _tokens(e.get("title"))) / len(ht),
            0.85 * len(ht & _tokens(e.get("description"))) / len(ht),
        )

    scored = sorted(((score(e), i, e) for i, e in enumerate(episodes)), key=lambda t: (-t[0], t[1]))
    best = scored[0]
    second = scored[1][0] if len(scored) > 1 else 0.0
    if best[0] >= 0.55 and best[0] - second >= 0.08:
        return best[2], [], "title match (%.2f)" % best[0]
    return None, [e for _, _, e in scored[:5]], 'no episode clearly matches "%s"' % text


def resolve(api, episode_id=None, podcast=None, hint=None):
    """Return (episode_detail or None, podcast, candidates, reason)."""
    if episode_id:
        if not SHA256.match(episode_id.strip()):
            raise CliError("episode_id must be the 64-character hex id from `episodes`.", "invalid_request")
        ep = api.get("/episodes/" + episode_id.strip())
        return ep, ep.get("podcast"), [], "by episode_id"
    link = parse_link(podcast) or parse_link(hint)
    query, itunes_id = (podcast or "").strip(), None
    if link:
        if link["kind"] == "apple":
            itunes_id = link["itunes_id"]
            query = link["show"] or ("" if query.startswith("http") else query)
            if hint and link["url"] in hint:
                hint = None
        elif link["kind"] == "spotify":
            raise CliError(
                'Spotify links cannot be resolved yet; pass --podcast "<show name>" '
                '--episode "<episode title>" instead.',
                "unsupported_link",
            )
        else:
            raise CliError(
                'PodderSum searches by show name, not by feed/web URL; pass --podcast "<show name>".',
                "unsupported_link",
            )
    if not query:
        raise CliError("Give --episode-id or --podcast.", "invalid_request")
    results = api.get("/search", {"q": query, "limit": 5}).get("results") or []
    if not results:
        raise CliError('No podcast with a public RSS feed matched "%s".' % query, "not_found")
    show = next((r for r in results if itunes_id and r.get("itunes_id") == itunes_id), None) or next(
        (r for r in results if _norm(r.get("title")) == _norm(query)), results[0]
    )
    listing = api.get("/podcasts/%s/episodes" % show["id"], {"limit": 100})
    episodes = listing.get("episodes") or []
    if link and link.get("episode") and not hint:
        return (
            None,
            show,
            episodes[:5],
            (
                "the Apple link points at one specific episode, which "
                "PodderSum cannot map yet; confirm which episode"
            ),
        )
    ep, candidates, reason = pick_episode(episodes, hint)
    if ep is None:
        return None, show, candidates, reason
    return api.get("/episodes/" + ep["id"]), show, [], reason


def ensure_ready(api, ep, wait_s):
    """Return (ready, job). Prints progress to stderr."""
    if ep.get("status") == "ready":
        return True, None
    job = api.post("/episodes/%s/ingest" % ep["id"])
    if job.get("state") == "succeeded":
        return True, job
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        try:
            for event, ev in api.stream(
                "GET",
                "/jobs/%s/events" % job["job_id"],
                timeout=max(1.0, min(120.0, deadline - time.monotonic())),
            ):
                if time.monotonic() >= deadline:
                    break
                if event != "job":
                    continue
                sys.stderr.write(
                    "[%3d%%] %s %s\n"
                    % (round((ev.get("progress") or 0) * 100), ev.get("step"), ev.get("message") or "")
                )
                if ev.get("state") == "succeeded":
                    return True, dict(job, **ev)
                if ev.get("state") == "failed":
                    final = api.get("/jobs/" + job["job_id"])
                    raise CliError(
                        "Transcribing the episode failed: %s. Run again to retry."
                        % (final.get("error") or ev.get("message")),
                        "ingest_failed",
                    )
        except CliError as exc:
            if exc.code != "timeout":
                raise
        except OSError:  # read timeout or dropped connection: check the job row and retry
            pass
        final = api.get("/jobs/" + job["job_id"])
        if final.get("state") == "succeeded":
            return True, final
    return False, api.get("/jobs/" + job["job_id"])


def collect(events):
    claims, answer = [], None
    for _name, ev in events:
        if ev is None:
            continue
        kind = ev.get("event")
        if kind == "status":
            sys.stderr.write("... %s\n" % ev.get("message", ""))
        elif kind == "claim":
            claims.append(ev["claim"])
        elif kind == "answer":
            answer = ev["answer"]
        elif kind == "error":
            raise CliError(ev.get("message") or "The answer failed.", ev.get("code", "error"))
        elif kind == "done":
            break
    if answer is None:
        if not claims:
            raise CliError("The answer stream ended without an answer.", "empty_answer")
        answer = {"claims": claims, "cached": False}
    return answer


# --- commands -----------------------------------------------------------------------


def processing(ep, job):
    job = job or {}
    text = (
        "%s\n\nStill transcribing (%d%%, step `%s`, job `%s`). Run the same command again with "
        "--episode-id %s in a minute."
        % (heading(ep), round((job.get("progress") or 0) * 100), job.get("step"), job.get("job_id"), ep["id"])
    )
    return EXIT_PROCESSING, text, {"status": "processing", "episode_id": ep["id"], "job": job}


def cmd_search(api, a):
    res = api.get("/search", {"q": a.query, "limit": a.limit}).get("results") or []
    if not res:
        return EXIT_OK, 'No podcasts matched "%s".' % a.query, {"results": []}
    lines = ['Podcasts matching "%s":' % a.query] + [
        "%d. **%s**%s - podcast_id `%s`"
        % (i, p["title"], " (%s)" % p["author"] if p.get("author") else "", p["id"])
        for i, p in enumerate(res, 1)
    ]
    return EXIT_OK, "\n".join(lines), {"results": res}


def cmd_episodes(api, a):
    data = api.get("/podcasts/%s/episodes" % a.podcast_id, {"limit": a.limit})
    lines = ["**%s** - newest first:" % data["podcast"]["title"]] + [
        episode_line(e, i) for i, e in enumerate(data.get("episodes") or [], 1)
    ]
    return EXIT_OK, "\n".join(lines), data


def cmd_episode(api, a):
    ep = api.get("/episodes/" + a.episode_id)
    text = "%s\nepisode_id `%s` · status: %s\naudio: %s" % (
        heading(ep),
        ep["id"],
        ep.get("status"),
        ep.get("audio_url"),
    )
    if ep.get("description"):
        text += "\n\n" + ep["description"][:600]
    return EXIT_OK, text, ep


def cmd_job(api, a):
    job = api.get("/jobs/" + a.job_id)
    text = "Job `%s`: %s at step `%s` (%d%%)%s" % (
        job["job_id"],
        job["state"],
        job["step"],
        round((job.get("progress") or 0) * 100),
        "\nError: " + job["error"] if job.get("error") else "",
    )
    return EXIT_OK, text, job


def model_unavailable(exc):
    return exc.code.startswith("llm_") or exc.status == 503


def segment_line(seg):
    start = int(seg.get("start_ms") or 0)
    speaker = "%s: " % seg["speaker"] if seg.get("speaker") else ""
    return "[%s] %s (t=%d) %s%s" % (
        seg.get("id"),
        timestamp(start),
        start // 1000,
        speaker,
        " ".join(str(seg.get("text") or "").split()),
    )


def fetch_transcript(api, ep, start_ms=None, end_ms=None, max_chars=DEFAULT_TRANSCRIPT_CHARS):
    """Segments from start_ms until max_chars (PROPOSED route GET /episodes/{id}/transcript)."""
    segments, used, cursor, total, next_start = [], 0, start_ms, None, None
    while True:
        params = {"limit": 500}
        if cursor is not None:
            params["start_ms"] = cursor
        if end_ms is not None:
            params["end_ms"] = end_ms
        try:
            page = api.get("/episodes/%s/transcript" % ep["id"], params)
        except CliError as exc:
            if exc.status in (404, 405) and exc.code == "http_error":
                raise CliError(
                    "This PodderSum API has no transcript route yet (GET /episodes/{id}/"
                    "transcript), so the host model cannot read the transcript.",
                    "api_gap",
                ) from None
            raise
        total = page.get("total_segments", total)
        full = False
        for seg in page.get("segments") or []:
            n = len(segment_line(seg)) + 1
            if segments and used + n > max_chars:
                next_start, full = int(seg["start_ms"]), True
                break
            segments.append(seg)
            used += n
        if full or page.get("next_start_ms") is None or not page.get("segments"):
            break
        cursor = int(page["next_start_ms"])
    return {
        "episode_id": ep["id"],
        "audio_url": ep.get("audio_url"),
        "link_base": (ep.get("audio_url") or "").split("#", 1)[0] + "#t=",
        "total_segments": total,
        "segments": segments,
        "next_start_ms": next_start,
    }


def transcript_output(ep, page, task, reason):
    segs = page["segments"]
    lines = [
        heading(ep),
        "episode_id `%s` · audio: %s" % (ep["id"], ep.get("audio_url")),
        "",
        "**%s** Task for you (the host model): %s" % (reason, task),
        CITATION_RULES % page["link_base"],
        "",
        "Transcript segments (%d of %s): `[segment_id] m:ss (t=seconds) Speaker: text`"
        % (len(segs), page.get("total_segments")),
    ]
    lines += [segment_line(s) for s in segs]
    if page.get("next_start_ms") is not None:
        lines += [
            "",
            "More transcript follows: run `transcript --episode-id %s --start-ms %d` and "
            "continue before answering." % (ep["id"], page["next_start_ms"]),
        ]
    data = {
        "status": "transcript",
        "reason": reason,
        "task": task,
        "episode_id": ep["id"],
        "title": ep.get("title"),
        "transcript": page,
    }
    return EXIT_OK, "\n".join(lines), data


def host_fallback(api, ep, task, reason, server_error=None):
    try:
        page = fetch_transcript(api, ep)
    except CliError as exc:
        if exc.code == "api_gap" and server_error:
            raise CliError(
                "PodderSum's server-side model is unavailable (%s) and %s" % (server_error, exc.message),
                "no_engine",
            ) from None
        raise
    return transcript_output(ep, page, task, reason)


def unavailable_hint(ep, exc, what):
    hint = (
        "PodderSum's server-side model is unavailable (%s: %s). Fall back to the host model: run "
        "`transcript --episode-id %s` and write the %s yourself, ending every claim with a "
        "[m:ss](audio_url#t=seconds) link taken from the segments (never invented)."
        % (exc.code, exc.message, ep["id"], what)
    )
    return (
        EXIT_ERROR,
        hint,
        {"status": "model_unavailable", "code": exc.code, "hint": hint, "episode_id": ep["id"]},
    )


def cmd_transcript(api, a):
    ep, show, candidates, reason = resolve(api, a.episode_id, a.podcast, a.episode)
    if ep is None:
        return choice(show, candidates, reason)
    ready, job = ensure_ready(api, ep, a.wait)
    if not ready:
        return processing(ep, job)
    page = fetch_transcript(api, ep, a.start_ms, a.end_ms, a.max_chars)
    return transcript_output(
        ep,
        page,
        "Use these segments to summarize or answer what the user asked.",
        "Transcript for the host model.",
    )


def choice(show, candidates, reason):
    lines = [
        "Matched podcast **%s**, but %s. Ask the user which episode they mean, then run again "
        "with --episode-id:" % (show.get("title"), reason)
    ]
    lines += [episode_line(c, i) for i, c in enumerate(candidates, 1)]
    return (
        EXIT_CHOICE,
        "\n".join(lines),
        {"status": "needs_choice", "reason": reason, "candidates": candidates},
    )


def cmd_summarize(api, a):
    ep, show, candidates, reason = resolve(api, a.episode_id, a.podcast, a.episode)
    if ep is None:
        return choice(show, candidates, reason)
    ready, job = ensure_ready(api, ep, a.wait)
    if not ready:
        return processing(ep, job)
    task = HOST_TASKS[a.style] + (
        " Angle: %s - cover only what is relevant to it." % a.angle if a.angle else ""
    )
    if a.engine == "host":
        return host_fallback(
            api, ep, task, "Host-model mode: write the summary yourself from the transcript below."
        )
    try:
        if a.angle:
            question = (
                STYLE_QUESTIONS[a.style]
                + " Answer from this angle, and only cover what is relevant to it: "
                + a.angle
            )
            answer = collect(api.stream("POST", "/episodes/%s/ask" % ep["id"], {"question": question}))
            source = "ask"
        else:
            answer = api.get("/episodes/%s/artifacts/%s" % (ep["id"], a.style), timeout=180)["answer"]
            source = "artifact"
    except CliError as exc:
        if not model_unavailable(exc):
            raise
        if a.engine == "server":
            return unavailable_hint(ep, exc, "summary")
        return host_fallback(
            api,
            ep,
            task,
            "PodderSum's server-side model is unavailable (%s), so write "
            "the summary yourself from the transcript below." % exc.code,
            "%s: %s" % (exc.code, exc.message),
        )
    title = STYLE_TITLES[a.style] + (" (angle: %s)" % a.angle if a.angle else "")
    text = "%s\n\n**%s**\n%s\n\nepisode_id `%s`" % (
        heading(ep),
        title,
        claims_md(answer.get("claims"), ep.get("audio_url")),
        ep["id"],
    )
    return (
        EXIT_OK,
        text,
        {
            "status": "ok",
            "style": a.style,
            "angle": a.angle,
            "source": source,
            "episode_id": ep["id"],
            "title": ep.get("title"),
            "answer": linked(answer, ep.get("audio_url")),
        },
    )


def _converse(api, a, mode):
    ep, _show, _c, _r = resolve(api, a.episode_id)
    ready, job = ensure_ready(api, ep, a.wait)
    if not ready:
        return processing(ep, job)
    task = "Answer this question from the transcript: " + a.text
    if a.engine == "host":
        return host_fallback(api, ep, task, "Host-model mode: answer yourself from the transcript below.")
    path = "/episodes/%s/%s" % (ep["id"], mode)
    body = {"question": a.text} if mode == "ask" else {"message": a.text}
    try:
        answer = collect(api.stream("POST", path, body))
    except CliError as exc:
        if not model_unavailable(exc):
            raise
        if a.engine == "server":
            return unavailable_hint(ep, exc, "answer")
        return host_fallback(
            api,
            ep,
            task,
            "PodderSum's server-side model is unavailable (%s), so answer "
            "yourself from the transcript below." % exc.code,
            "%s: %s" % (exc.code, exc.message),
        )
    label = "Q" if mode == "ask" else "You"
    text = "%s\n\n**%s: %s**\n%s" % (
        heading(ep),
        label,
        a.text,
        claims_md(answer.get("claims"), ep.get("audio_url")),
    )
    return (
        EXIT_OK,
        text,
        {"status": "ok", "mode": mode, "episode_id": ep["id"], "answer": linked(answer, ep.get("audio_url"))},
    )


def build_parser():
    p = argparse.ArgumentParser(
        prog="poddersum.py", description="PodderSum podcast summaries with timestamp links."
    )
    p.add_argument("--api-url", default=os.environ.get("PODDERSUM_API_URL") or DEFAULT_API_URL)
    p.add_argument("--json", action="store_true", help="Print JSON instead of markdown.")
    p.add_argument("--version", action="version", version=VERSION)
    try:
        default_wait = float(os.environ.get("PODDERSUM_WAIT_S") or 240)
    except ValueError:
        default_wait = 240.0
    default_engine = (os.environ.get("PODDERSUM_ENGINE") or "auto").strip().lower()
    if default_engine not in ENGINES:
        default_engine = "auto"
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("search", help="Search podcasts by name.")
    s.add_argument("query")
    s.add_argument("--limit", type=int, default=5)
    s.set_defaults(fn=cmd_search)
    s = sub.add_parser("episodes", help="List a podcast's episodes (newest first).")
    s.add_argument("podcast_id")
    s.add_argument("--limit", type=int, default=10)
    s.set_defaults(fn=cmd_episodes)
    s = sub.add_parser("episode", help="Show one episode.")
    s.add_argument("episode_id")
    s.set_defaults(fn=cmd_episode)
    s = sub.add_parser("summarize", help="Summarize an episode (transcribes it first if needed).")
    s.add_argument("--episode-id")
    s.add_argument("--podcast", help="Show name or Apple Podcasts link.")
    s.add_argument("--episode", help="Episode title words, 'latest' (default) or a number like '#212'.")
    s.add_argument("--style", choices=STYLES, default="tldr")
    s.add_argument("--angle", help="Optional focus, e.g. 'for a founder'.")
    s.add_argument("--wait", type=float, default=default_wait, help="Max seconds to wait for transcription.")
    s.add_argument(
        "--engine",
        choices=ENGINES,
        default=default_engine,
        help="auto (default): server model, else transcript for you; host: transcript; server: no fallback.",
    )
    s.set_defaults(fn=cmd_summarize)
    s = sub.add_parser(
        "transcript", help="Print an episode's timestamped transcript (for host-model summaries)."
    )
    s.add_argument("--episode-id")
    s.add_argument("--podcast", help="Show name or Apple Podcasts link.")
    s.add_argument("--episode", help="Episode title words, 'latest' (default) or a number.")
    s.add_argument("--start-ms", type=int)
    s.add_argument("--end-ms", type=int)
    s.add_argument("--max-chars", type=int, default=DEFAULT_TRANSCRIPT_CHARS)
    s.add_argument("--wait", type=float, default=default_wait)
    s.set_defaults(fn=cmd_transcript)
    for mode, helptext in (
        ("ask", "Ask one question about an episode."),
        ("chat", "Chat about an episode (keeps history)."),
    ):
        s = sub.add_parser(mode, help=helptext)
        s.add_argument("episode_id")
        s.add_argument("text")
        s.add_argument("--wait", type=float, default=default_wait)
        s.add_argument("--engine", choices=ENGINES, default=default_engine)
        s.set_defaults(fn=lambda api, a, m=mode: _converse(api, a, m))
    s = sub.add_parser("job", help="Show an ingest job's status.")
    s.add_argument("job_id")
    s.set_defaults(fn=cmd_job)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    api = Api(args.api_url, os.environ.get("PODDERSUM_API_KEY") or None)
    try:
        code, text, data = args.fn(api, args)
    except CliError as exc:
        if args.json:
            print(json.dumps({"status": "error", "code": exc.code, "message": exc.message}))
        else:
            sys.stderr.write("Error: %s\n" % exc.message)
        return EXIT_ERROR
    print(json.dumps(data, indent=2) if args.json else text)
    return code


if __name__ == "__main__":
    sys.exit(main())
