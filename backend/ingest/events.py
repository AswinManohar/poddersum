"""In-process wake-ups for job event streams.

Correctness comes from Postgres (``job_events``): SSE streams poll the table at a short
interval. This notifier only lets a stream in the same process wake immediately when the
runner writes an event, instead of waiting for the next poll.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import defaultdict


class JobNotifier:
    def __init__(self) -> None:
        self._waiters: defaultdict[str, set[asyncio.Event]] = defaultdict(set)

    def notify(self, job_id: str) -> None:
        for ev in self._waiters.get(job_id, ()):
            ev.set()

    async def wait(self, job_id: str, timeout_s: float) -> None:
        """Return when ``job_id`` gets an event, or after ``timeout_s``."""
        ev = asyncio.Event()
        self._waiters[job_id].add(ev)
        try:
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(timeout_s):
                    await ev.wait()
        finally:
            waiters = self._waiters.get(job_id)
            if waiters is not None:
                waiters.discard(ev)
                if not waiters:
                    self._waiters.pop(job_id, None)
