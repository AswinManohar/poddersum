"""``GET /episodes/{episode_id}/artifacts/{kind}``: a cached preset answer (JSON)."""

from __future__ import annotations

from fastapi import APIRouter, Request

from agent import errors as agent_errors
from agent.errors import AgentError
from agent.presets import get_or_create_artifact
from api.deps import ApiError, SessionDep, errors
from api.routes.ask import agent_deps, ensure_episode_ready
from api.routes.common import EpisodeIdPath
from api.schemas import Artifact, ArtifactKind

router = APIRouter(tags=["answers"])

# Answer-engine error code -> HTTP status. Anything else is an upstream (model) failure.
ERROR_STATUS = {
    agent_errors.NOT_READY: 409,
    agent_errors.TRANSCRIPT_TOO_LONG: 409,
    agent_errors.LLM_AUTH: 503,
    agent_errors.LLM_CONFIG: 503,
}
UPSTREAM_STATUS = 502


@router.get(
    "/episodes/{episode_id}/artifacts/{kind}",
    response_model=Artifact,
    responses=errors(404, 409, 502, 503),
    summary="Get a preset answer (TL;DR, takeaways, verdict)",
)
async def get_artifact(
    episode_id: EpisodeIdPath, kind: ArtifactKind, request: Request, session: SessionDep
) -> Artifact:
    """Generated on first request and cached per (episode, kind, prompt_version); later
    requests make no model call (`answer.cached = true`). For `verdict`, claims carry
    `section` = `who_its_for` | `whats_new` | `best_10_minutes`. 409 when the episode has
    no transcript yet; 502 when the model fails, 503 when it is unavailable (auth/config)."""
    await ensure_episode_ready(session, episode_id)
    await session.close()  # the model call can take seconds; do not hold the connection
    try:
        return await get_or_create_artifact(agent_deps(request), episode_id, kind)
    except AgentError as exc:
        raise ApiError(ERROR_STATUS.get(exc.code, UPSTREAM_STATUS), exc.code, exc.message) from None
