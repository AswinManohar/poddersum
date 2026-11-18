"""Failures of the answer engine, as stable codes with user-safe messages.

``message`` is shown to users (SSE ``error`` event, ``ErrorResponse.detail``) and never
contains provider output or secrets. ``detail`` is for logs and ``requests.error``; it
comes from the gateway's already-redacted exception text.
"""

from __future__ import annotations

from llm import LLMAuthError, LLMConfigError, LLMError, LLMRateLimitError, LLMStructuredOutputError

NOT_READY = "not_ready"
TRANSCRIPT_TOO_LONG = "transcript_too_long"
NO_GROUNDED_CLAIMS = "no_grounded_claims"
LLM_AUTH = "llm_auth"
LLM_CONFIG = "llm_config"
LLM_RATE_LIMITED = "llm_rate_limited"
LLM_BAD_OUTPUT = "llm_bad_output"
LLM_ERROR = "llm_error"


class AgentError(Exception):
    def __init__(self, code: str, message: str, detail: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail or message


def not_ready() -> AgentError:
    return AgentError(NOT_READY, "This episode has no transcript yet.")


def no_grounded_claims() -> AgentError:
    return AgentError(
        NO_GROUNDED_CLAIMS,
        "Could not ground an answer in this episode: nothing the model wrote could be tied "
        "to a moment in the transcript.",
    )


# Checked in order: subclasses before LLMError.
_LLM_ERRORS: list[tuple[type[LLMError], str, str]] = [
    (
        LLMAuthError,
        LLM_AUTH,
        "The AI model provider rejected our API key, so answers are unavailable right now.",
    ),
    (
        LLMConfigError,
        LLM_CONFIG,
        "The AI model is not configured correctly, so answers are unavailable right now.",
    ),
    (LLMRateLimitError, LLM_RATE_LIMITED, "The AI model is busy. Please try again in a minute."),
    (
        LLMStructuredOutputError,
        LLM_BAD_OUTPUT,
        "The AI model returned an answer we could not read. Please try again.",
    ),
    (LLMError, LLM_ERROR, "The AI model failed to answer. Please try again."),
]


def from_llm_error(exc: LLMError) -> AgentError:
    for error_type, code, message in _LLM_ERRORS:
        if isinstance(exc, error_type):
            return AgentError(code, message, detail=str(exc))
    raise AssertionError("LLMError is the last entry")
