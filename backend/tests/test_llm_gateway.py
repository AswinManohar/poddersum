"""Gateway behaviour against a fake provider (no network)."""

from __future__ import annotations

import base64

import httpx
import pytest
from pydantic import BaseModel

from core.settings import Settings
from core.tracing import Tracer
from llm.config import ModelsConfig
from llm.errors import LLMAuthError, LLMError, LLMStructuredOutputError, UnknownTaskError
from llm.fake import FakeProvider, FakeReply, provider_errors
from llm.gateway import Gateway, LLMResult

FAKE_MODEL = "fake/fake-lite-1"
FAKE_KEY = "fake-provider-key-123"
MSGS = [{"role": "user", "content": "hi"}]


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None, GEMINI_API_KEY=FAKE_KEY)


@pytest.fixture
def config() -> ModelsConfig:
    return ModelsConfig.model_validate(
        {
            "tasks": {
                "chat": {"model": "lite", "params": {"max_tokens": 100}, "max_retries": 2},
                "stt_cheap": {"model": "lite", "params": {}},
            },
            "models": {
                "lite": {
                    "litellm_model": FAKE_MODEL,
                    "provider": "gemini",
                    "prices_per_1m_usd": {
                        "input_text": 0.25,
                        "input_audio": 0.50,
                        "output": 1.50,
                        "cached_input_text": 0.025,
                        "cached_input_audio": 0.05,
                    },
                }
            },
        }
    )


async def _no_sleep(_: float) -> None:
    return None


def make_gateway(settings: Settings, config: ModelsConfig, fake: FakeProvider) -> Gateway:
    return Gateway(
        settings=settings,
        config=config,
        provider=fake,
        tracer=Tracer(settings, client_factory=_explode),
        sleep=_no_sleep,
    )


def _explode(_: Settings) -> None:
    raise AssertionError("tracing client must not be constructed in these tests")


def _err(cls: type[Exception], status: int, message: str = "boom") -> Exception:
    req = httpx.Request("POST", "https://example.invalid")
    resp = httpx.Response(status, request=req)
    return cls(message=message, llm_provider="gemini", model=FAKE_MODEL, response=resp)


async def test_complete_returns_text_tokens_cost(settings, config):
    fake = FakeProvider([FakeReply("hello there", tokens_in=1000, tokens_out=200, cached=400)])
    result = await make_gateway(settings, config, fake).complete(
        "chat", MSGS, metadata={"feature": "test", "episode_id": "ep1"}
    )
    assert result.text == "hello there"
    assert result.model == "fake-lite-1"
    assert result.task == "chat"
    assert (result.tokens_in, result.tokens_out, result.tokens_cached) == (1000, 200, 400)
    # 600 fresh text * 0.25 + 400 cached * 0.025 + 200 out * 1.50, per 1M
    assert result.cost_usd == pytest.approx((600 * 0.25 + 400 * 0.025 + 200 * 1.5) / 1e6)
    assert result.latency_ms >= 0
    call = fake.calls[0]
    assert call["model"] == FAKE_MODEL
    assert call["api_key"] == FAKE_KEY
    assert call["max_tokens"] == 100
    assert call["num_retries"] == 0


async def test_params_override_task_defaults(settings, config):
    fake = FakeProvider()
    await make_gateway(settings, config, fake).complete("chat", MSGS, params={"max_tokens": 7})
    assert fake.calls[0]["max_tokens"] == 7


async def test_unknown_task_raises(settings, config):
    fake = FakeProvider()
    with pytest.raises(UnknownTaskError, match="nope"):
        await make_gateway(settings, config, fake).complete("nope", MSGS)
    assert fake.calls == []
    assert issubclass(UnknownTaskError, LLMError)


async def test_stream_yields_deltas_then_final_result(settings, config):
    fake = FakeProvider([FakeReply("abcdefgh", tokens_in=50, tokens_out=8, chunk_size=3)])
    items = [i async for i in make_gateway(settings, config, fake).stream("chat", MSGS)]
    deltas, final = items[:-1], items[-1]
    assert deltas == ["abc", "def", "gh"]
    assert isinstance(final, LLMResult)
    assert final.text == "abcdefgh"
    assert (final.tokens_in, final.tokens_out) == (50, 8)
    assert final.cost_usd == pytest.approx((50 * 0.25 + 8 * 1.5) / 1e6)
    assert fake.calls[0]["stream"] is True
    assert fake.calls[0]["stream_options"] == {"include_usage": True}


class Verdict(BaseModel):
    who: str
    score: int


async def test_structured_parses_valid_json(settings, config):
    fake = FakeProvider(['```json\n{"who": "founders", "score": 3}\n```'])
    parsed, result = await make_gateway(settings, config, fake).structured("chat", MSGS, Verdict)
    assert parsed == Verdict(who="founders", score=3)
    assert result.parsed == parsed
    fmt = fake.calls[0]["response_format"]
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["name"] == "Verdict"
    assert "score" in fmt["json_schema"]["schema"]["properties"]


async def test_structured_repairs_once(settings, config):
    fake = FakeProvider(
        [
            FakeReply('{"who": "x"}', tokens_in=10, tokens_out=4),
            FakeReply('{"who": "x", "score": 1}', tokens_in=30, tokens_out=6),
        ]
    )
    parsed, result = await make_gateway(settings, config, fake).structured("chat", MSGS, Verdict)
    assert parsed.score == 1
    assert len(fake.calls) == 2
    repair = fake.calls[1]["messages"]
    assert repair[-2] == {"role": "assistant", "content": '{"who": "x"}'}
    assert "did not match" in repair[-1]["content"]
    # tokens and cost cover both calls
    assert (result.tokens_in, result.tokens_out, result.attempts) == (40, 10, 2)


async def test_structured_fails_after_one_repair(settings, config):
    fake = FakeProvider(["not json", "still not json", '{"who": "x", "score": 1}'])
    with pytest.raises(LLMStructuredOutputError, match="Verdict"):
        await make_gateway(settings, config, fake).structured("chat", MSGS, Verdict)
    assert len(fake.calls) == 2


async def test_transcribe_audio_message_shape(settings, config):
    audio = b"\x00\x01fake-mp3-bytes"
    fake = FakeProvider(
        [FakeReply("[00:00] Host: hi", tokens_in=1100, tokens_out=50, audio_in=1000)]
    )
    result = await make_gateway(settings, config, fake).transcribe_audio(
        "stt_cheap", audio, "audio/mpeg", "Transcribe with timestamps."
    )
    content = fake.calls[0]["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "Transcribe with timestamps."}
    assert content[1]["type"] == "file"
    file_data = content[1]["file"]["file_data"]
    assert file_data.startswith("data:audio/mpeg;base64,")
    assert base64.b64decode(file_data.split(",", 1)[1]) == audio
    assert content[1]["file"]["format"] == "audio/mpeg"
    # 1000 audio at 0.50, 100 text at 0.25, 50 out at 1.50
    assert result.tokens_audio_in == 1000
    assert result.cost_usd == pytest.approx((1000 * 0.5 + 100 * 0.25 + 50 * 1.5) / 1e6)


async def test_transcribe_audio_with_schema(settings, config):
    fake = FakeProvider(['{"who": "host", "score": 9}'])
    result = await make_gateway(settings, config, fake).transcribe_audio(
        "stt_cheap", b"abc", "audio/wav", "p", schema=Verdict
    )
    assert result.parsed == Verdict(who="host", score=9)
    assert "response_format" in fake.calls[0]


async def test_transcribe_rejects_non_audio(settings, config):
    with pytest.raises(LLMError, match="audio"):
        await make_gateway(settings, config, FakeProvider()).transcribe_audio(
            "stt_cheap", b"x", "text/plain", "p"
        )


async def test_retry_on_429_then_success(settings, config):
    fake = FakeProvider([_err(provider_errors.RateLimitError, 429), "recovered"])
    result = await make_gateway(settings, config, fake).complete("chat", MSGS)
    assert result.text == "recovered"
    assert result.attempts == 2
    assert len(fake.calls) == 2


async def test_retry_on_5xx_is_bounded(settings, config):
    fake = FakeProvider([_err(provider_errors.InternalServerError, 500)])
    with pytest.raises(LLMError, match="InternalServerError"):
        await make_gateway(settings, config, fake).complete("chat", MSGS)
    assert len(fake.calls) == 3  # 1 + max_retries(2)


async def test_auth_error_mapped_without_retry_or_secret(settings, config):
    exc = _err(
        provider_errors.AuthenticationError,
        401,
        'GeminiException - {"reason": "API_KEY_INVALID"} key=' + FAKE_KEY,
    )
    fake = FakeProvider([exc])
    with pytest.raises(LLMAuthError) as info:
        await make_gateway(settings, config, fake).complete("chat", MSGS)
    msg = str(info.value)
    assert msg == "Gemini rejected GEMINI_API_KEY (API_KEY_INVALID); replace the key in .env"
    assert FAKE_KEY not in msg
    assert len(fake.calls) == 1


async def test_bad_request_with_invalid_key_reason_is_auth(settings, config):
    fake = FakeProvider([_err(provider_errors.BadRequestError, 400, "API_KEY_INVALID")])
    with pytest.raises(LLMAuthError):
        await make_gateway(settings, config, fake).complete("chat", MSGS)


async def test_other_errors_redact_key(settings, config):
    fake = FakeProvider([ValueError(f"failed calling ?key={FAKE_KEY}&alt=sse")])
    with pytest.raises(LLMError) as info:
        await make_gateway(settings, config, fake).complete("chat", MSGS)
    assert FAKE_KEY not in str(info.value)
