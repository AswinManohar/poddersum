"""Model gateway: every LLM call in PodderSum goes through here, keyed by task.

- Model ids and prices come only from ``llm/models.yaml`` (see ``llm.config``).
- Calls go through LiteLLM (``litellm.acompletion``) by default; the provider callable is
  injectable so tests and later slices can plug a fake (see ``llm.fake.FakeProvider``).
- Each call returns an ``LLMResult`` with text, tokens, cost (from our yaml) and latency,
  and is traced to LangSmith when configured (``core.tracing``).
"""

from __future__ import annotations

import asyncio
import base64
import logging
import random
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from core.settings import Settings, get_settings
from core.tracing import Tracer, get_tracer
from llm.config import ModelsConfig, ModelSpec, TaskSpec, get_config
from llm.errors import (
    LLMAuthError,
    LLMError,
    LLMRateLimitError,
    LLMStructuredOutputError,
    UnknownTaskError,
)
from llm.pricing import compute_cost

__all__ = [
    "CallMetadata",
    "Gateway",
    "LLMAuthError",
    "LLMError",
    "LLMResult",
    "LLMStructuredOutputError",
    "Provider",
    "UnknownTaskError",
    "complete",
    "get_gateway",
    "stream",
    "structured",
    "transcribe_audio",
]

log = logging.getLogger(__name__)

Message = Mapping[str, Any]
Provider = Callable[..., Awaitable[Any]]
"""Same calling convention as ``litellm.acompletion`` (keyword arguments)."""
Sleep = Callable[[float], Awaitable[None]]

# provider name in models.yaml -> Settings attribute holding its API key
_PROVIDER_KEYS = {"gemini": "GEMINI_API_KEY"}


class LLMResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    text: str
    model: str  # resolved provider model id from models.yaml
    task: str
    tokens_in: int = 0  # all prompt tokens (incl. audio and cached)
    tokens_out: int = 0  # completion tokens incl. thinking
    tokens_cached: int = 0
    tokens_audio_in: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    attempts: int = 1  # provider calls made (retries and structured repairs included)
    parsed: Any | None = None  # set by structured()/transcribe_audio(schema=...)
    raw: Any | None = Field(default=None, repr=False, exclude=True)


@dataclass(frozen=True)
class CallMetadata:
    """Attribution for one call; becomes LangSmith tags and metadata."""

    feature: str | None = None  # defaults to the task key
    prompt_version: str | None = None
    episode_id: str | None = None
    extra: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def coerce(cls, value: CallMetadata | Mapping[str, Any] | None) -> CallMetadata:
        if value is None:
            return cls()
        if isinstance(value, CallMetadata):
            return value
        data = dict(value)
        known = {k: data.pop(k) for k in ("feature", "prompt_version", "episode_id") if k in data}
        return cls(**known, extra=data)


MetadataArg = CallMetadata | Mapping[str, Any] | None


# --------------------------------------------------------------------------- helpers


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


@dataclass(frozen=True)
class _Usage:
    tokens_in: int = 0
    tokens_out: int = 0
    cached: int = 0
    cached_audio: int = 0
    audio_in: int = 0

    @classmethod
    def from_response(cls, usage: Any) -> _Usage:
        details = _get(usage, "prompt_tokens_details")
        cached = _int(_get(details, "cached_tokens")) or _int(
            _get(usage, "cache_read_input_tokens")
        )
        return cls(
            tokens_in=_int(_get(usage, "prompt_tokens")),
            tokens_out=_int(_get(usage, "completion_tokens")),
            cached=cached,
            cached_audio=_int(_get(_get(details, "cached_tokens_details"), "audio_tokens")),
            audio_in=_int(_get(details, "audio_tokens")),
        )

    def __add__(self, other: _Usage) -> _Usage:
        return _Usage(
            self.tokens_in + other.tokens_in,
            self.tokens_out + other.tokens_out,
            self.cached + other.cached,
            self.cached_audio + other.cached_audio,
            self.audio_in + other.audio_in,
        )


def _message_text(response: Any) -> str:
    choices = _get(response, "choices") or []
    if not choices:
        return ""
    content = _get(_get(choices[0], "message"), "content")
    if isinstance(content, list):  # some providers return content parts
        return "".join(str(_get(p, "text", "")) for p in content)
    return content or ""


def _sanitize_messages(messages: Sequence[Message]) -> list[dict[str, Any]]:
    """Copy of messages safe for traces: binary/audio parts replaced with a placeholder."""
    out: list[dict[str, Any]] = []
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            parts: list[Any] = []
            for part in content:
                ptype = _get(part, "type")
                if ptype in ("file", "input_file", "input_audio", "image_url"):
                    parts.append({"type": ptype, "data": _describe_media(part)})
                else:
                    parts.append(part)
            out.append({**msg, "content": parts})
        else:
            out.append(dict(msg))
    return out


_DATA_URI = re.compile(r"^data:(?P<mime>[^;,]*)(?:;base64)?,")


def _describe_media(part: Any) -> str:
    inner = _get(part, "file") or _get(part, "input_audio") or _get(part, "image_url") or {}
    data = _get(inner, "file_data") or _get(inner, "data") or _get(inner, "url") or ""
    mime = _get(inner, "format") or "unknown"
    if not isinstance(data, str):
        return f"<media omitted: {mime}>"
    if m := _DATA_URI.match(data):
        mime = m.group("mime") or mime
        data = data[m.end() :]
    return f"<media omitted: ~{len(data) * 3 // 4} bytes, {mime}>"


_JSON_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.S)


def _strip_fences(text: str) -> str:
    m = _JSON_FENCE.match(text)
    return m.group(1) if m else text.strip()


def _json_schema_format(schema: type[BaseModel]) -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema()},
    }


# --------------------------------------------------------------------------- error mapping


def _litellm_errors() -> dict[str, tuple[type[BaseException], ...]]:
    import litellm

    return {
        "auth": (litellm.AuthenticationError, litellm.PermissionDeniedError),
        "rate": (litellm.RateLimitError,),
        "retryable": (
            litellm.RateLimitError,
            litellm.InternalServerError,
            litellm.ServiceUnavailableError,
            litellm.BadGatewayError,
            litellm.APIConnectionError,
            litellm.Timeout,
        ),
    }


_RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}


def _status(exc: BaseException) -> int | None:
    code = getattr(exc, "status_code", None)
    return code if isinstance(code, int) else None


# --------------------------------------------------------------------------- gateway


class Gateway:
    """Task-keyed async model gateway.

    Args:
        settings: app settings (default: ``get_settings()``, resolved lazily).
        config: parsed ``models.yaml`` (default: ``get_config()``).
        provider: async callable with ``litellm.acompletion``'s keyword convention.
        tracer: LangSmith tracer (default: one built from ``settings``).
        sleep: awaited between retries (inject a no-op in tests).
    """

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        config: ModelsConfig | None = None,
        provider: Provider | None = None,
        tracer: Tracer | None = None,
        sleep: Sleep = asyncio.sleep,
        backoff_base_s: float = 0.5,
    ) -> None:
        self._settings = settings
        self.config = config or get_config()
        self._provider = provider
        self._tracer = tracer
        self._sleep = sleep
        self._backoff_base_s = backoff_base_s

    # -- wiring ------------------------------------------------------------------------

    @property
    def settings(self) -> Settings:
        if self._settings is None:
            self._settings = get_settings()
        return self._settings

    @property
    def tracer(self) -> Tracer:
        if self._tracer is None:
            self._tracer = Tracer(self._settings) if self._settings else get_tracer()
        return self._tracer

    @property
    def provider(self) -> Provider:
        if self._provider is None:
            import litellm

            litellm.suppress_debug_info = True
            self._provider = litellm.acompletion
        return self._provider

    def _api_key(self, model: ModelSpec) -> str:
        attr = _PROVIDER_KEYS.get(model.provider)
        if attr is None:
            raise LLMError(f"no API key mapping for provider {model.provider!r}")
        return getattr(self.settings, attr).get_secret_value()

    def _redact(self, text: str, api_key: str) -> str:
        if api_key:
            text = text.replace(api_key, "***")
        return re.sub(r"([?&]key=)[^&\s\"']+", r"\1***", text)[:500]

    def _request(
        self,
        task: TaskSpec,
        model: ModelSpec,
        messages: Sequence[Message],
        params: Mapping[str, Any] | None,
        **extra: Any,
    ) -> dict[str, Any]:
        return {
            **task.params,
            **(params or {}),
            **extra,
            "model": model.litellm_model,
            "messages": list(messages),
            "api_key": self._api_key(model),
            "timeout": task.timeout_s,
            "num_retries": 0,  # retries are ours, bounded and typed
        }

    def _map_error(self, exc: BaseException, api_key: str, model: ModelSpec) -> LLMError:
        errs = _litellm_errors()
        msg = self._redact(str(exc), api_key)
        if isinstance(exc, errs["auth"]) or "API_KEY_INVALID" in msg or _status(exc) in (401, 403):
            env = _PROVIDER_KEYS.get(model.provider, "API key")
            reason = " (API_KEY_INVALID)" if "API_KEY_INVALID" in msg else ""
            return LLMAuthError(
                f"{model.provider.capitalize()} rejected {env}{reason}; replace the key in .env"
            )
        if isinstance(exc, errs["rate"]) or _status(exc) == 429:
            return LLMRateLimitError(f"rate limited by {model.provider} after retries: {msg}")
        return LLMError(f"{type(exc).__name__}: {msg}")

    def _retryable(self, exc: BaseException) -> bool:
        if isinstance(exc, LLMAuthError):
            return False
        return isinstance(exc, _litellm_errors()["retryable"]) or _status(exc) in _RETRYABLE_STATUS

    async def _backoff(self, attempt: int) -> None:
        delay = self._backoff_base_s * (2**attempt) * (0.75 + random.random() / 2)
        await self._sleep(delay)

    async def _call(
        self, task: TaskSpec, model: ModelSpec, request: dict[str, Any]
    ) -> tuple[Any, int]:
        """Call the provider with bounded retries. Returns (response, attempts)."""
        api_key = request["api_key"]
        for attempt in range(task.max_retries + 1):
            try:
                return await self.provider(**request), attempt + 1
            except LLMError:
                raise
            except Exception as exc:
                if attempt < task.max_retries and self._retryable(exc):
                    log.info(
                        "llm retry %d/%d after %s",
                        attempt + 1,
                        task.max_retries,
                        type(exc).__name__,
                    )
                    await self._backoff(attempt)
                    continue
                raise self._map_error(exc, api_key, model) from None
        raise AssertionError("unreachable")

    def _result(
        self,
        task_key: str,
        model: ModelSpec,
        text: str,
        usage: _Usage,
        started: float,
        attempts: int,
        raw: Any,
        parsed: Any = None,
    ) -> LLMResult:
        cost = compute_cost(
            model.prices_per_1m_usd,
            input_tokens=usage.tokens_in,
            output_tokens=usage.tokens_out,
            audio_input_tokens=usage.audio_in,
            cached_tokens=usage.cached,
            cached_audio_tokens=usage.cached_audio,
        )
        return LLMResult(
            text=text,
            model=model.model_id,
            task=task_key,
            tokens_in=usage.tokens_in,
            tokens_out=usage.tokens_out,
            tokens_cached=usage.cached,
            tokens_audio_in=usage.audio_in,
            cost_usd=cost,
            latency_ms=int((time.perf_counter() - started) * 1000),
            attempts=attempts,
            parsed=parsed,
            raw=raw,
        )

    def _trace_args(
        self, op: str, task_key: str, model: ModelSpec, meta: CallMetadata, inputs: dict[str, Any]
    ) -> dict[str, Any]:
        feature = meta.feature or task_key
        md: dict[str, Any] = {
            "feature": feature,
            "task": task_key,
            "model": model.model_id,
            "prompt_version": meta.prompt_version,
            "episode_id": meta.episode_id,
            "ls_provider": model.provider,
            "ls_model_name": model.model_id,
            **dict(meta.extra),
        }
        tags = [f"feature:{feature}", f"task:{task_key}", f"model:{model.model_id}", f"op:{op}"]
        if meta.prompt_version:
            tags.append(f"prompt_version:{meta.prompt_version}")
        if meta.episode_id:
            tags.append(f"episode_id:{meta.episode_id}")
        return {
            "name": f"llm.{op}.{task_key}",
            "inputs": inputs,
            "tags": tags,
            "metadata": {k: v for k, v in md.items() if v is not None},
        }

    @staticmethod
    def _trace_outputs(result: LLMResult) -> tuple[dict[str, Any], dict[str, Any]]:
        usage = {
            "input_tokens": result.tokens_in,
            "output_tokens": result.tokens_out,
            "total_tokens": result.tokens_in + result.tokens_out,
            "input_token_details": {
                "cache_read": result.tokens_cached,
                "audio": result.tokens_audio_in,
            },
            "total_cost": result.cost_usd,
        }
        outputs = {
            "choices": [{"message": {"role": "assistant", "content": result.text}}],
            "usage_metadata": usage,
        }
        md = {
            "tokens_in": result.tokens_in,
            "tokens_out": result.tokens_out,
            "tokens_cached": result.tokens_cached,
            "cost_usd": result.cost_usd,
            "latency_ms": result.latency_ms,
            "attempts": result.attempts,
        }
        return outputs, md

    # -- public API --------------------------------------------------------------------

    async def complete(
        self,
        task: str,
        messages: Sequence[Message],
        *,
        metadata: MetadataArg = None,
        params: Mapping[str, Any] | None = None,
    ) -> LLMResult:
        """One non-streaming completion for ``task``."""
        return await self._complete(task, messages, metadata=metadata, params=params, op="complete")

    async def _complete(
        self,
        task: str,
        messages: Sequence[Message],
        *,
        metadata: MetadataArg,
        params: Mapping[str, Any] | None,
        op: str,
        trace_messages: Sequence[Message] | None = None,
    ) -> LLMResult:
        spec, model = self.config.resolve(task)
        meta = CallMetadata.coerce(metadata)
        inputs = {"messages": _sanitize_messages(trace_messages or messages)}
        async with self.tracer.run(**self._trace_args(op, task, model, meta, inputs)) as run:
            started = time.perf_counter()
            response, attempts = await self._call(
                spec, model, self._request(spec, model, messages, params)
            )
            result = self._result(
                task,
                model,
                _message_text(response),
                _Usage.from_response(_get(response, "usage")),
                started,
                attempts,
                response,
            )
            run.end(*self._trace_outputs(result))
        return result

    async def stream(
        self,
        task: str,
        messages: Sequence[Message],
        *,
        metadata: MetadataArg = None,
        params: Mapping[str, Any] | None = None,
    ) -> AsyncIterator[str | LLMResult]:
        """Yield text deltas (``str``), then exactly one final ``LLMResult``."""
        spec, model = self.config.resolve(task)
        meta = CallMetadata.coerce(metadata)
        inputs = {"messages": _sanitize_messages(messages)}
        request = self._request(
            spec, model, messages, params, stream=True, stream_options={"include_usage": True}
        )
        async with self.tracer.run(**self._trace_args("stream", task, model, meta, inputs)) as run:
            started = time.perf_counter()
            chunks, attempts = await self._call(spec, model, request)
            parts: list[str] = []
            usage: Any = None
            try:
                async for chunk in chunks:
                    if (u := _get(chunk, "usage")) is not None:
                        usage = u
                    for choice in _get(chunk, "choices") or []:
                        delta = _get(_get(choice, "delta"), "content")
                        if delta:
                            parts.append(delta)
                            yield delta
            except LLMError:
                raise
            except Exception as exc:  # mid-stream failure: no retry once text was emitted
                raise self._map_error(exc, request["api_key"], model) from None
            result = self._result(
                task,
                model,
                "".join(parts),
                _Usage.from_response(usage),
                started,
                attempts,
                None,
            )
            run.end(*self._trace_outputs(result))
        yield result

    async def structured[T: BaseModel](
        self,
        task: str,
        messages: Sequence[Message],
        schema: type[T],
        *,
        metadata: MetadataArg = None,
        params: Mapping[str, Any] | None = None,
    ) -> tuple[T, LLMResult]:
        """JSON-schema constrained completion, validated with Pydantic.

        On invalid output, makes one repair call (the bad reply plus the validation error).
        The returned ``LLMResult`` sums tokens and cost across both calls.
        """
        parsed, result = await self._structured(task, messages, schema, metadata, params, None)
        return parsed, result

    async def _structured[T: BaseModel](
        self,
        task: str,
        messages: Sequence[Message],
        schema: type[T],
        metadata: MetadataArg,
        params: Mapping[str, Any] | None,
        trace_messages: Sequence[Message] | None,
        op: str = "structured",
    ) -> tuple[T, LLMResult]:
        spec, model = self.config.resolve(task)
        meta = CallMetadata.coerce(metadata)
        inputs = {
            "messages": _sanitize_messages(trace_messages or messages),
            "schema": schema.__name__,
        }
        fmt = {"response_format": _json_schema_format(schema)}
        async with self.tracer.run(**self._trace_args(op, task, model, meta, inputs)) as run:
            started = time.perf_counter()
            convo = list(messages)
            usage = _Usage()
            attempts = 0
            error: str = ""
            text = ""
            response: Any = None
            for round_ in range(2):  # first try + one bounded repair
                response, n = await self._call(
                    spec, model, self._request(spec, model, convo, params, **fmt)
                )
                attempts += n
                usage = usage + _Usage.from_response(_get(response, "usage"))
                text = _message_text(response)
                try:
                    parsed = schema.model_validate_json(_strip_fences(text))
                except ValidationError as exc:
                    error = str(exc)[:2000]
                    if round_ == 0:
                        convo += [
                            {"role": "assistant", "content": text},
                            {
                                "role": "user",
                                "content": (
                                    "Your previous reply did not match the required JSON schema."
                                    f"\nValidation error:\n{error}\n"
                                    "Reply again with only valid JSON matching the schema."
                                ),
                            },
                        ]
                    continue
                result = self._result(
                    task, model, text, usage, started, attempts, response, parsed=parsed
                )
                run.end(*self._trace_outputs(result))
                return parsed, result
            result = self._result(task, model, text, usage, started, attempts, response)
            run.end(*self._trace_outputs(result))
            raise LLMStructuredOutputError(
                f"task {task!r}: output did not match {schema.__name__} after one repair: "
                f"{error[:300]}"
            )

    async def transcribe_audio(
        self,
        task: str,
        audio_bytes: bytes,
        mime_type: str,
        prompt: str,
        *,
        metadata: MetadataArg = None,
        schema: type[BaseModel] | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> LLMResult:
        """Send ``audio_bytes`` inline (base64 file part) with ``prompt``.

        With ``schema``, output is JSON-constrained and validated; the parsed model is on
        ``result.parsed``. Audio tokens are priced at the model's audio input rate.
        Raw audio never reaches traces (replaced with a size/mime placeholder).
        """
        if not mime_type.startswith("audio/"):
            raise LLMError(f"transcribe_audio expects an audio/* mime type, got {mime_type!r}")
        b64 = base64.b64encode(audio_bytes).decode("ascii")
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "file",
                        "file": {
                            "file_data": f"data:{mime_type};base64,{b64}",
                            "format": mime_type,
                        },
                    },
                ],
            }
        ]
        trace_messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "audio", "data": f"<audio: {len(audio_bytes)} bytes, {mime_type}>"},
                ],
            }
        ]
        if schema is None:
            return await self._complete(
                task,
                messages,
                metadata=metadata,
                params=params,
                op="transcribe",
                trace_messages=trace_messages,
            )
        _, result = await self._structured(
            task, messages, schema, metadata, params, trace_messages, op="transcribe"
        )
        return result


# --------------------------------------------------------------------------- module API


@lru_cache
def get_gateway() -> Gateway:
    """Process-wide gateway on LiteLLM. Tests should build ``Gateway(provider=FakeProvider())``."""
    return Gateway()


async def complete(
    task: str, messages: Sequence[Message], *, metadata: MetadataArg = None
) -> LLMResult:
    return await get_gateway().complete(task, messages, metadata=metadata)


def stream(
    task: str, messages: Sequence[Message], *, metadata: MetadataArg = None
) -> AsyncIterator[str | LLMResult]:
    return get_gateway().stream(task, messages, metadata=metadata)


async def structured[T: BaseModel](
    task: str, messages: Sequence[Message], schema: type[T], *, metadata: MetadataArg = None
) -> tuple[T, LLMResult]:
    return await get_gateway().structured(task, messages, schema, metadata=metadata)


async def transcribe_audio(
    task: str,
    audio_bytes: bytes,
    mime_type: str,
    prompt: str,
    *,
    metadata: MetadataArg = None,
    schema: type[BaseModel] | None = None,
) -> LLMResult:
    return await get_gateway().transcribe_audio(
        task, audio_bytes, mime_type, prompt, metadata=metadata, schema=schema
    )
