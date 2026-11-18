"""PodderSum MCP server (official ``mcp`` SDK, ``MCPServer`` - formerly FastMCP).

Run ``poddersum-mcp`` (stdio, default) or ``poddersum-mcp --transport http --port 8765``
(Streamable HTTP at ``/mcp``). Every tool returns compact markdown for the model plus the
same data as ``structuredContent``.
"""

from __future__ import annotations

import argparse
import logging
from typing import Annotated, Literal

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp_types import CallToolResult, TextContent, ToolAnnotations
from pydantic import Field

from poddersum_mcp import __version__, service
from poddersum_mcp import format as fmt
from poddersum_mcp.client import JSON, PodderSumClient, PodderSumError
from poddersum_mcp.config import MAX_WAIT_S, Config

logger = logging.getLogger(__name__)

INSTRUCTIONS = """\
PodderSum summarizes podcast episodes from their transcript; every claim carries timestamp
links into the original audio. No API key is needed. For "summarize / what's this episode
about / is it worth it" requests call summarize_episode with podcast_name + episode_hint (or
episode_id); it searches, transcribes if needed and answers in one call. It either returns a
finished summary (status=ok) or, when PodderSum has no server-side model or engine='host' is
used, the transcript (status=transcript): then YOU write the summary from the segments, ending
every claim with a [m:ss](audio_url#t=seconds) link copied from a segment, never invented.
status=needs_choice: ask the user which candidate they mean. Use ask_episode for one-off
questions, chat_episode for a running conversation, get_transcript to read (more of) an
episode yourself."""

mcp = MCPServer(
    name="poddersum",
    title="PodderSum",
    description="Search podcasts and get cited, timestamped episode summaries and answers.",
    instructions=INSTRUCTIONS,
    version=__version__,
)

READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=True)
# Starts transcription / model calls on the PodderSum backend; not destructive.
ACTION = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True)

EpisodeId = Annotated[
    str,
    Field(
        description="64-char hex episode id from list_episodes, get_episode or summarize_episode.",
        pattern=r"^[0-9a-f]{64}$",
    ),
]
EngineArg = Annotated[
    Literal["auto", "host", "server"] | None,
    Field(
        default=None,
        description="Who writes the answer. 'auto' (default): PodderSum's server model if it has "
        "one, else the transcript is returned for you to write it. 'host': always return the "
        "transcript so you (the host model) write it, e.g. when the user says 'use my own "
        "model'. 'server': server model only.",
    ),
]
WaitS = Annotated[
    float | None,
    Field(
        default=None,
        ge=1,
        le=MAX_WAIT_S,
        description="Max seconds to wait for transcription before returning status=processing "
        "(default from PODDERSUM_WAIT_S, 240). Calling again resumes the same job.",
    ),
]


def _config() -> Config:
    return Config.from_env()


def _result(text: str, data: JSON) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=text)], structured_content=data)


def _progress(ctx: Context | None) -> service.ProgressFn:
    async def report(progress: float, message: str) -> None:
        if ctx is None:
            return
        try:
            await ctx.report_progress(round(progress, 3), 1.0, message or None)
        except Exception:  # noqa: BLE001 - progress is best effort, never fail the tool
            logger.debug("progress notification failed", exc_info=True)

    return report


async def _run(coro_factory) -> CallToolResult:  # type: ignore[no-untyped-def]
    config = _config()
    try:
        async with PodderSumClient(config) as client:
            text, data = await coro_factory(client, config)
            return _result(text, data)
    except PodderSumError as exc:
        raise ToolError(exc.message) from None


# --- discovery -----------------------------------------------------------------


@mcp.tool(
    title="Search podcasts",
    description=(
        "Search public podcasts (shows) by name or keywords. Returns up to `limit` shows with "
        "their podcast_id, needed by list_episodes. Not needed before summarize_episode, which "
        "searches by itself when given podcast_name."
    ),
    annotations=READ_ONLY,
)
async def search_podcasts(
    query: Annotated[str, Field(min_length=1, max_length=200, description="Show name or keywords.")],
    limit: Annotated[int, Field(ge=1, le=20, description="Max shows to return.")] = 5,
) -> CallToolResult:
    async def run(client: PodderSumClient, _c: Config) -> tuple[str, JSON]:
        data = await client.search(query, limit=limit)
        results = data.get("results") or []
        return fmt.podcasts_markdown(results, query), {"query": query, "results": results}

    return await _run(run)


@mcp.tool(
    title="List episodes",
    description=(
        "List a podcast's episodes, newest first, with episode_id, date, duration and "
        "transcript status. podcast_id comes from search_podcasts."
    ),
    annotations=READ_ONLY,
)
async def list_episodes(
    podcast_id: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$", description="From search_podcasts.")],
    limit: Annotated[int, Field(ge=1, le=100, description="Max episodes to return.")] = 10,
) -> CallToolResult:
    async def run(client: PodderSumClient, _c: Config) -> tuple[str, JSON]:
        data = await client.list_episodes(podcast_id, limit=limit)
        return fmt.episodes_markdown(data), data

    return await _run(run)


@mcp.tool(
    title="Get episode",
    description="Get one episode's details: title, show, date, duration, audio URL, show notes "
    "and transcript status (new | processing | ready | failed).",
    annotations=READ_ONLY,
)
async def get_episode(episode_id: EpisodeId) -> CallToolResult:
    async def run(client: PodderSumClient, _c: Config) -> tuple[str, JSON]:
        res = await service.resolve_episode(client, episode_id=episode_id)
        ep = res.episode or {}
        return fmt.episode_markdown(ep), ep

    return await _run(run)


# --- answers -------------------------------------------------------------------


@mcp.tool(
    title="Summarize a podcast episode",
    description=(
        "One-call podcast episode summary with timestamp citations. Identify the episode by "
        "episode_id, OR by podcast_name (show name or Apple Podcasts link) plus episode_hint "
        "(episode title words, 'latest', or an episode number). Transcribes the episode if "
        "needed (may take minutes; progress is reported), then returns the summary. "
        "style: 'tldr' (short summary), 'takeaways' (key lessons), 'verdict' (is it worth "
        "listening: who it's for, what's new, best 10 minutes). angle: optional focus such as "
        "'for a founder' or 'only the parts about sleep'. Returns status=needs_choice with "
        "candidates when the episode is ambiguous (ask the user), status=processing with a "
        "job id when transcription outlasts wait_s (call again to resume), or "
        "status=transcript with timestamped segments when the summary is for you to write "
        "(no server model, or engine='host'). No API key needed."
    ),
    annotations=ACTION,
)
async def summarize_episode(
    ctx: Context,
    episode_id: Annotated[
        str | None, Field(description="Episode id, if already known. Overrides the name fields.")
    ] = None,
    podcast_name: Annotated[
        str | None, Field(description="Show name (e.g. 'Hidden Brain') or an Apple Podcasts link.")
    ] = None,
    episode_hint: Annotated[
        str | None,
        Field(description="Which episode: title keywords, 'latest' (default), or a number like '#212'."),
    ] = None,
    style: Annotated[Literal["tldr", "takeaways", "verdict"], Field(description="Summary preset.")] = "tldr",
    angle: Annotated[
        str | None,
        Field(max_length=500, description="Optional focus or audience, e.g. 'for a product manager'."),
    ] = None,
    engine: EngineArg = None,
    wait_s: WaitS = None,
) -> CallToolResult:
    async def run(client: PodderSumClient, config: Config) -> tuple[str, JSON]:
        return await service.summarize(
            client,
            episode_id=episode_id,
            podcast_name=podcast_name,
            episode_hint=episode_hint,
            style=style,
            angle=angle,
            wait_s=wait_s or config.wait_s,
            engine=engine or config.engine,
            progress=_progress(ctx),
        )

    return await _run(run)


@mcp.tool(
    title="Ask about an episode",
    description=(
        "Ask one question about a podcast episode and get an answer grounded in its "
        "transcript, every claim linked to a timestamp. Stateless (no memory of earlier "
        "questions; use chat_episode for a conversation). Transcribes first if needed. Without "
        "a server model (or with engine='host') it returns status=transcript: answer yourself "
        "from the segments, citing them."
    ),
    annotations=ACTION,
)
async def ask_episode(
    ctx: Context,
    episode_id: EpisodeId,
    question: Annotated[str, Field(min_length=1, max_length=2000)],
    engine: EngineArg = None,
    wait_s: WaitS = None,
) -> CallToolResult:
    async def run(client: PodderSumClient, config: Config) -> tuple[str, JSON]:
        return await service.converse(
            client, episode_id, question, mode="ask", engine=engine or config.engine,
            wait_s=wait_s or config.wait_s, progress=_progress(ctx),
        )  # fmt: skip

    return await _run(run)


@mcp.tool(
    title="Chat about an episode",
    description=(
        "Send a message in the episode's ongoing chat (the server remembers earlier turns for "
        "this episode, so follow-ups like 'and what about the second guest?' work). Replies "
        "are grounded in the transcript with timestamp links. Transcribes first if needed. "
        "Without a server model (or engine='host') it returns status=transcript instead: "
        "answer yourself from the segments (the server keeps no history in that case)."
    ),
    annotations=ACTION,
)
async def chat_episode(
    ctx: Context,
    episode_id: EpisodeId,
    message: Annotated[str, Field(min_length=1, max_length=2000)],
    engine: EngineArg = None,
    wait_s: WaitS = None,
) -> CallToolResult:
    async def run(client: PodderSumClient, config: Config) -> tuple[str, JSON]:
        return await service.converse(
            client, episode_id, message, mode="chat", engine=engine or config.engine,
            wait_s=wait_s or config.wait_s, progress=_progress(ctx),
        )  # fmt: skip

    return await _run(run)


@mcp.tool(
    title="Get job status",
    description="Check a transcription job by job_id (from summarize_episode status=processing): "
    "state (queued | running | succeeded | failed), step and progress.",
    annotations=READ_ONLY,
)
async def get_job_status(
    job_id: Annotated[str, Field(min_length=1, max_length=64, description="Job UUID.")],
) -> CallToolResult:
    async def run(client: PodderSumClient, _c: Config) -> tuple[str, JSON]:
        job = await client.get_job(job_id)
        return fmt.job_markdown(job), job

    return await _run(run)


@mcp.tool(
    title="Get an episode transcript",
    description=(
        "Read an episode's timestamped transcript so you (the host model) can summarize or "
        "answer from it yourself; no API key or server model needed. Transcribes first if "
        "needed. Lines are `[segment_id] m:ss (t=seconds) Speaker: text`; cite claims as "
        "[m:ss](audio_url#t=seconds) using a segment's start, never invented. Long episodes "
        "are paginated: when next_start_ms is set, call again with start_ms=next_start_ms. "
        "Use start_ms/end_ms to read one stretch. Identify the episode by episode_id or by "
        "podcast_name + episode_hint."
    ),
    annotations=ACTION,
)
async def get_transcript(
    ctx: Context,
    episode_id: Annotated[str | None, Field(description="Episode id, if already known.")] = None,
    podcast_name: Annotated[str | None, Field(description="Show name or Apple Podcasts link.")] = None,
    episode_hint: Annotated[str | None, Field(description="Title words, 'latest' or '#212'.")] = None,
    start_ms: Annotated[int | None, Field(ge=0, description="Start of the stretch, ms.")] = None,
    end_ms: Annotated[int | None, Field(ge=0, description="End of the stretch, ms.")] = None,
    max_chars: Annotated[
        int,
        Field(ge=1000, le=service.MAX_TRANSCRIPT_CHARS, description="Max transcript characters."),
    ] = service.DEFAULT_TRANSCRIPT_CHARS,
    wait_s: WaitS = None,
) -> CallToolResult:
    async def run(client: PodderSumClient, config: Config) -> tuple[str, JSON]:
        return await service.get_transcript(
            client,
            episode_id=episode_id,
            podcast_name=podcast_name,
            episode_hint=episode_hint,
            start_ms=start_ms,
            end_ms=end_ms,
            max_chars=max_chars,
            wait_s=wait_s or config.wait_s,
            progress=_progress(ctx),
        )

    return await _run(run)


# --- prompt --------------------------------------------------------------------


@mcp.prompt(
    name="summarize_podcast",
    title="Summarize a podcast episode",
    description="Summarize a podcast episode with PodderSum, with timestamp links.",
)
def summarize_podcast(
    podcast: str, episode: str = "latest", angle: str = "", style: str = "tldr", engine: str = "auto"
) -> str:
    focus = f' Focus: "{angle}".' if angle.strip() else ""
    return (
        f'Summarize the podcast episode "{episode}" of "{podcast}" for me (style: {style}).{focus}\n\n'
        "Workflow: call the PodderSum tool summarize_episode with podcast_name, episode_hint, "
        f"style, angle and engine='{engine}'. If it returns status=transcript, write the summary "
        "yourself from the segments, ending every claim with a [m:ss](audio_url#t=seconds) link "
        "copied from a segment (page with get_transcript if next_start_ms is set). "
        "If it returns status=needs_choice, show me the candidates and ask "
        "which one I mean. If it returns status=processing, tell me it is still transcribing "
        "and call it again with the episode_id. Present the result as short bullets and keep "
        "every [m:ss](link) timestamp link exactly as returned. End with one line offering "
        "follow-up questions (ask_episode / chat_episode)."
    )


# --- entry point ---------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="poddersum-mcp", description=__doc__.splitlines()[0])
    parser.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    parser.add_argument("--host", default="127.0.0.1", help="HTTP bind host (default 127.0.0.1).")
    parser.add_argument("--port", type=int, default=8765, help="HTTP port (default 8765).")
    parser.add_argument(
        "--allowed-host",
        action="append",
        default=[],
        metavar="HOST[:PORT]",
        help="Extra Host header to accept over HTTP (e.g. your tunnel's hostname). Repeatable.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)  # no per-request lines on stderr
    if args.transport == "stdio":
        mcp.run("stdio")
        return
    security = None
    if args.allowed_host:
        local = ["127.0.0.1", "localhost", "[::1]"]
        hosts = [*args.allowed_host, *local, *(f"{h}:{args.port}" for h in local)]
        security = TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=hosts)
    mcp.run(
        "streamable-http",
        host=args.host,
        port=args.port,
        streamable_http_path="/mcp",
        transport_security=security,
    )


if __name__ == "__main__":
    main()
