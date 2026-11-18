"""Typed errors raised by the model gateway. Messages never contain secret values."""

from __future__ import annotations


class LLMError(RuntimeError):
    """Any failure in the model gateway."""


class UnknownTaskError(LLMError, KeyError):
    """The task key is not defined in ``models.yaml``."""

    def __str__(self) -> str:  # KeyError would otherwise repr() the message
        return str(self.args[0]) if self.args else ""


class LLMConfigError(LLMError):
    """``models.yaml`` is missing or invalid."""


class LLMAuthError(LLMError):
    """The provider rejected the API key."""


class LLMRateLimitError(LLMError):
    """Still rate limited after the bounded retries."""


class LLMStructuredOutputError(LLMError):
    """The model did not return JSON matching the schema, even after one repair attempt."""
