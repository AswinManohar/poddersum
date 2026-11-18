"""Scriptable fake provider for tests (no network). Returns real LiteLLM response types.

Example (inside an async test)::

    fake = FakeProvider([FakeReply("hello", tokens_in=10, tokens_out=2)])
    gw = Gateway(settings=settings, provider=fake, sleep=no_sleep)
    result = await gw.complete("chat", [{"role": "user", "content": "hi"}])
    fake.calls[0]["model"]  # request kwargs as sent to litellm.acompletion
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from typing import Any

from litellm import ModelResponse, Usage

# Re-exported so tests can build provider errors without importing litellm themselves
# (ruff TID251 bans that outside llm/: it would bypass llm/__init__'s LITELLM_MODE guard).
from litellm import exceptions as provider_errors  # noqa: F401
from litellm.types.utils import (
    Delta,
    ModelResponseStream,
    PromptTokensDetailsWrapper,
    StreamingChoices,
)


@dataclass(frozen=True)
class FakeReply:
    text: str
    tokens_in: int = 10
    tokens_out: int = 5
    cached: int = 0
    audio_in: int = 0
    chunk_size: int = 3  # characters per streamed delta


def _usage(reply: FakeReply) -> Usage:
    return Usage(
        prompt_tokens=reply.tokens_in,
        completion_tokens=reply.tokens_out,
        total_tokens=reply.tokens_in + reply.tokens_out,
        prompt_tokens_details=PromptTokensDetailsWrapper(
            cached_tokens=reply.cached or None, audio_tokens=reply.audio_in or None
        ),
    )


class FakeProvider:
    """Async callable with ``litellm.acompletion``'s keyword convention.

    Each call consumes the next scripted item: a ``FakeReply`` (or str) is returned,
    an exception instance is raised. The last item repeats when the script runs out.
    """

    def __init__(self, script: Iterable[FakeReply | str | BaseException] = ("ok",)) -> None:
        self.script = [FakeReply(s) if isinstance(s, str) else s for s in script]
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        item = self.script[min(len(self.calls) - 1, len(self.script) - 1)]
        if isinstance(item, BaseException):
            raise item
        if kwargs.get("stream"):
            return self._stream(item, include_usage=bool(kwargs.get("stream_options")))
        return ModelResponse(
            model=kwargs.get("model", "fake"),
            choices=[
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": item.text},
                }
            ],
            usage=_usage(item),
        )

    async def _stream(self, reply: FakeReply, *, include_usage: bool) -> AsyncIterator[Any]:
        step = max(1, reply.chunk_size)
        for i in range(0, len(reply.text), step):
            yield ModelResponseStream(
                choices=[StreamingChoices(delta=Delta(content=reply.text[i : i + step]))]
            )
        if include_usage:
            yield ModelResponseStream(
                choices=[StreamingChoices(delta=Delta(content=None), finish_reason="stop")],
                usage=_usage(reply),
            )
