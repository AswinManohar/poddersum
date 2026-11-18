"""Minimal server-sent-events parser (enough for PodderSum's job and answer streams)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass


@dataclass
class SseFrame:
    event: str
    data: str
    id: str | None = None


async def parse_sse(lines: AsyncIterator[str]) -> AsyncIterator[SseFrame]:
    """Yield one frame per blank-line-terminated block. Comment lines (pings) are ignored."""
    event, data, frame_id = "message", [], None
    async for raw in lines:
        line = raw.rstrip("\r\n")
        if not line:
            if data:
                yield SseFrame(event=event, data="\n".join(data), id=frame_id)
            event, data, frame_id = "message", [], None
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        value = value.removeprefix(" ")
        if field == "event":
            event = value
        elif field == "data":
            data.append(value)
        elif field == "id":
            frame_id = value
    if data:
        yield SseFrame(event=event, data="\n".join(data), id=frame_id)
