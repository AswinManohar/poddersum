"""What the answer engine needs from the outside world, passed in explicitly.

The API builds one :class:`AgentDeps` per request from ``app.state`` (tests swap in a
gateway on a fake provider). Nothing here holds per-request state.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent.transcript_context import SegmentRef
from db import queries
from llm import Gateway

ANSWER_TASK = "summary"
"""``models.yaml`` task key used for ask and presets."""

CHAT_TASK = "chat"
"""``models.yaml`` task key used for episode chat."""


@dataclass(frozen=True)
class AgentDeps:
    sessionmaker: async_sessionmaker[AsyncSession]
    gateway: Gateway

    async def load_segments(self, episode_id: str) -> list[SegmentRef]:
        """The episode's stored segments in order (empty when there is no transcript).
        Uses a short-lived session so no connection is held during the model call."""
        async with self.sessionmaker() as session:
            rows = await queries.get_episode_segments(session, episode_id)
            return [SegmentRef.from_row(r) for r in rows]

    def context_limits(self, task_key: str = ANSWER_TASK) -> tuple[int | None, int]:
        """(model context window, tokens reserved for the reply) for ``task_key``."""
        task, model = self.gateway.config.resolve(task_key)
        return model.context_window, int(task.params.get("max_tokens", 0))
