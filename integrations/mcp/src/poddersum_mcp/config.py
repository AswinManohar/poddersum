"""Configuration, from environment variables only (no files, no hardcoded hosts)."""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_API_URL = "http://localhost:8010"
# Below Hermes' default 300 s MCP tool timeout; a longer job is resumable (see service.py).
DEFAULT_WAIT_S = 240.0
MAX_WAIT_S = 1800.0
START_HINT = "cd backend && uv run uvicorn api.main:app --port 8010"


@dataclass(frozen=True)
class Config:
    api_url: str = DEFAULT_API_URL
    api_key: str | None = None
    wait_s: float = DEFAULT_WAIT_S
    engine: str = "auto"

    @classmethod
    def from_env(cls) -> Config:
        url = os.environ.get("PODDERSUM_API_URL", "").strip() or DEFAULT_API_URL
        key = os.environ.get("PODDERSUM_API_KEY", "").strip() or None
        try:
            wait = float(os.environ.get("PODDERSUM_WAIT_S", "") or DEFAULT_WAIT_S)
        except ValueError:
            wait = DEFAULT_WAIT_S
        engine = os.environ.get("PODDERSUM_ENGINE", "").strip().lower() or "auto"
        if engine not in ("auto", "host", "server"):
            engine = "auto"
        return cls(
            api_url=url.rstrip("/"), api_key=key, wait_s=min(max(wait, 1.0), MAX_WAIT_S), engine=engine
        )
