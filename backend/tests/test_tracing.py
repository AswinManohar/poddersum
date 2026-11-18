"""Tracing is silent when off and never breaks a model call when broken."""

from __future__ import annotations

import logging
import re
import warnings
from pathlib import Path
from typing import Any

import pytest

from core.settings import Settings
from core.tracing import Tracer
from llm.config import load_config
from llm.fake import FakeProvider
from llm.gateway import Gateway

MSGS = [{"role": "user", "content": "hi"}]


def _settings(**kw: Any) -> Settings:
    return Settings(_env_file=None, GEMINI_API_KEY="fake-provider-key-123", **kw)


class RecordingClient:
    """Stands in for langsmith.Client; RunTree only needs create_run/update_run."""

    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.updated: list[dict[str, Any]] = []
        self.flushed = 0

    def create_run(self, **kw: Any) -> None:
        self.created.append(kw)

    def update_run(self, run_id: Any, **kw: Any) -> None:
        self.updated.append({"run_id": run_id, **kw})

    def flush(self) -> None:
        self.flushed += 1


def _tracing_warnings(recwarn: pytest.WarningsRecorder) -> list[str]:
    """Warnings attributable to tracing: raised from langsmith or core/tracing.py, or about it.

    Unrelated warnings (e.g. pydantic ``ReadOnly`` UserWarnings litellm emits lazily on its
    first use in the process) are ignored so the assertion does not depend on test order.
    """
    tracing_files = ("langsmith", str(Path("core") / "tracing.py"))
    return [
        f"{w.category.__name__}: {w.message} ({w.filename}:{w.lineno})"
        for w in recwarn
        if any(part in w.filename for part in tracing_files)
        or re.search(r"langsmith|tracing|run_?tree", str(w.message), re.IGNORECASE)
    ]


async def _no_sleep(_: float) -> None:
    return None


def _gateway(settings: Settings, tracer: Tracer, fake: FakeProvider) -> Gateway:
    return Gateway(
        settings=settings, config=load_config(), provider=fake, tracer=tracer, sleep=_no_sleep
    )


@pytest.mark.parametrize(
    "kw",
    [
        {},  # LANGSMITH_* unset
        {"LANGSMITH_TRACING": False, "LANGSMITH_API_KEY": "ls-dummy"},
        {"LANGSMITH_TRACING": True},  # no key
    ],
)
async def test_tracing_off_constructs_no_client(kw, caplog, recwarn):
    calls: list[Settings] = []

    def factory(s: Settings) -> RecordingClient:
        calls.append(s)
        return RecordingClient()

    settings = _settings(**kw)
    tracer = Tracer(settings, client_factory=factory)
    caplog.set_level(logging.DEBUG)
    result = await _gateway(settings, tracer, FakeProvider(["ok"])).complete("chat", MSGS)
    tracer.flush()
    assert result.text == "ok"
    assert not tracer.enabled
    assert calls == []
    assert [r for r in caplog.records if r.name.startswith(("core.tracing", "langsmith"))] == []
    assert _tracing_warnings(recwarn) == []


async def test_traced_call_records_tags_metadata_tokens_cost():
    client = RecordingClient()
    settings = _settings(
        LANGSMITH_TRACING=True, LANGSMITH_API_KEY="ls-dummy", LANGSMITH_PROJECT="proj"
    )
    tracer = Tracer(settings, client_factory=lambda _: client)
    result = await _gateway(settings, tracer, FakeProvider(["traced"])).complete(
        "chat", MSGS, metadata={"feature": "ask", "prompt_version": "ask-v1", "episode_id": "ep9"}
    )
    tracer.flush()
    assert client.flushed == 1
    # RunTree batches create+end into create_run when it ends before posting completes,
    # or create_run then update_run; accept either.
    created = client.created[0]
    assert created["name"] == "llm.complete.chat"
    assert created["run_type"] == "llm"
    assert created["session_name"] == "proj"
    tags = set(created["tags"])
    assert {"feature:ask", "prompt_version:ask-v1", "episode_id:ep9", "task:chat"} <= tags
    md = created["extra"]["metadata"]
    assert md["feature"] == "ask" and md["episode_id"] == "ep9" and md["model"] == result.model
    final = client.updated[-1] if client.updated else created
    assert final["outputs"]["usage_metadata"]["total_cost"] == result.cost_usd
    assert final["outputs"]["usage_metadata"]["input_tokens"] == result.tokens_in
    assert "fake-provider-key-123" not in repr(client.created) + repr(client.updated)


async def test_audio_bytes_never_reach_trace_inputs():
    client = RecordingClient()
    settings = _settings(LANGSMITH_TRACING=True, LANGSMITH_API_KEY="ls-dummy")
    tracer = Tracer(settings, client_factory=lambda _: client)
    audio = b"SECRET-AUDIO-PAYLOAD" * 10
    await _gateway(settings, tracer, FakeProvider(["t"])).transcribe_audio(
        "stt_cheap", audio, "audio/mpeg", "transcribe"
    )
    dumped = repr(client.created) + repr(client.updated)
    import base64

    assert base64.b64encode(audio).decode()[:40] not in dumped
    assert "<audio: 200 bytes, audio/mpeg>" in dumped


async def test_tracing_errors_are_swallowed():
    class Broken(RecordingClient):
        def create_run(self, **kw: Any) -> None:
            raise RuntimeError("langsmith down")

        def update_run(self, run_id: Any, **kw: Any) -> None:
            raise RuntimeError("langsmith down")

        def flush(self) -> None:
            raise RuntimeError("langsmith down")

    settings = _settings(LANGSMITH_TRACING=True, LANGSMITH_API_KEY="ls-dummy")
    tracer = Tracer(settings, client_factory=lambda _: Broken())
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        result = await _gateway(settings, tracer, FakeProvider(["fine"])).complete("chat", MSGS)
        tracer.flush()
    assert result.text == "fine"


async def test_client_construction_failure_disables_tracing():
    def factory(_: Settings) -> Any:
        raise RuntimeError("bad config")

    settings = _settings(LANGSMITH_TRACING=True, LANGSMITH_API_KEY="ls-dummy")
    tracer = Tracer(settings, client_factory=factory)
    result = await _gateway(settings, tracer, FakeProvider(["fine"])).complete("chat", MSGS)
    assert result.text == "fine"
    assert not tracer.enabled
