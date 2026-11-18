"""PodderSum model gateway. Model ids live only in ``llm/models.yaml``."""

import os

# Must run before anything imports litellm:
# - LITELLM_MODE=DEV (its default) makes ``import litellm`` call ``dotenv.load_dotenv()``,
#   exporting every secret in the repo .env into os.environ (and silently turning on
#   LangSmith env-based tracing). Settings are the only config source here.
# - LITELLM_LOCAL_MODEL_COST_MAP avoids a network fetch of LiteLLM's price map at import;
#   costs come from models.yaml.
os.environ.setdefault("LITELLM_MODE", "PRODUCTION")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

# ruff: noqa: E402

from llm.errors import (
    LLMAuthError,
    LLMConfigError,
    LLMError,
    LLMRateLimitError,
    LLMStructuredOutputError,
    UnknownTaskError,
)
from llm.gateway import CallMetadata, Gateway, LLMResult, get_gateway

__all__ = [
    "CallMetadata",
    "Gateway",
    "LLMAuthError",
    "LLMConfigError",
    "LLMError",
    "LLMRateLimitError",
    "LLMResult",
    "LLMStructuredOutputError",
    "UnknownTaskError",
    "get_gateway",
]
