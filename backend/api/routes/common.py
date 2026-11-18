"""Path parameter types shared by routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import Path

EpisodeIdPath = Annotated[
    str, Path(pattern=r"^[0-9a-f]{64}$", description="Episode id (64-char hex).")
]
JobIdPath = Annotated[str, Path(min_length=1, max_length=64, description="Job id (UUID).")]
