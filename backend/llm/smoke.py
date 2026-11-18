"""Live smoke test for the model gateway.

    cd backend
    uv run python -m llm.smoke "hello"                 # task chat
    uv run python -m llm.smoke --task summary "hello"
    uv run python -m llm.smoke --stream "hello"
    uv run python -m llm.smoke --list-models           # confirm Flash-Lite ids with the API

Exit codes: 0 ok, 2 auth/config error, 1 other model error. Never prints secret values.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import httpx

from core.settings import Settings, SettingsError, get_settings
from core.tracing import Tracer
from llm.config import get_config
from llm.errors import LLMAuthError, LLMError
from llm.gateway import Gateway, LLMResult

MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models"
AUTH_HINT = "Gemini rejected GEMINI_API_KEY{reason}; replace the key in .env"


def _print_result(result: LLMResult, tracer: Tracer) -> None:
    print(f"text:      {result.text.strip()}")
    print(f"model:     {result.model} (task {result.task})")
    print(f"tokens:    in={result.tokens_in} out={result.tokens_out} cached={result.tokens_cached}")
    print(f"cost_usd:  {result.cost_usd:.8f}")
    print(f"latency:   {result.latency_ms} ms")
    if tracer.enabled:
        print(f"tracing:   LangSmith project {tracer.project_name or 'default'}")
    else:
        print("tracing:   off")


async def _run(settings: Settings, task: str, prompt: str, use_stream: bool) -> int:
    tracer = Tracer(settings)
    gateway = Gateway(settings=settings, tracer=tracer)
    messages = [{"role": "user", "content": prompt}]
    metadata = {"feature": "smoke", "prompt_version": "smoke-v1"}
    try:
        if use_stream:
            result: LLMResult | None = None
            async for item in gateway.stream(task, messages, metadata=metadata):
                if isinstance(item, LLMResult):
                    result = item
                else:
                    print(item, end="", flush=True)
            print()
            assert result is not None
        else:
            result = await gateway.complete(task, messages, metadata=metadata)
        _print_result(result, tracer)
        return 0
    except LLMAuthError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except LLMError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        tracer.flush()


def _list_models(settings: Settings) -> int:
    key = settings.GEMINI_API_KEY.get_secret_value()
    try:
        resp = httpx.get(
            MODELS_URL, params={"pageSize": 1000}, headers={"x-goog-api-key": key}, timeout=30
        )
    except httpx.HTTPError as exc:
        print(
            f"error: could not reach the Gemini models API ({type(exc).__name__})", file=sys.stderr
        )
        return 1
    if resp.status_code != 200:
        reason = " (API_KEY_INVALID)" if "API_KEY_INVALID" in resp.text else ""
        if reason or resp.status_code in (401, 403):
            print(f"error: {AUTH_HINT.format(reason=reason)}", file=sys.stderr)
            return 2
        print(f"error: models API returned HTTP {resp.status_code}", file=sys.stderr)
        return 1
    models = resp.json().get("models", [])
    lite = [m for m in models if "flash-lite" in m.get("name", "")]
    configured = {
        spec.model_id for spec in get_config().models.values() if spec.provider == "gemini"
    }
    print(f"{len(models)} models visible; Flash-Lite ids:")
    for m in lite:
        mid = m["name"].removeprefix("models/")
        mark = "  <- configured in models.yaml" if mid in configured else ""
        print(
            f"  {mid}  in={m.get('inputTokenLimit')} out={m.get('outputTokenLimit')}"
            f"  {m.get('displayName', '')}{mark}"
        )
    visible = {m["name"].removeprefix("models/") for m in models}
    for mid in sorted(configured - visible):
        print(f"WARNING: configured model {mid} is NOT in the models list", file=sys.stderr)
    return 0 if configured <= visible else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m llm.smoke", description=__doc__.split("\n")[0])
    parser.add_argument("prompt", nargs="?", default="hello")
    parser.add_argument("--task", default="chat", help="task key from llm/models.yaml")
    parser.add_argument("--stream", action="store_true", help="use the streaming path")
    parser.add_argument(
        "--list-models", action="store_true", help="list Flash-Lite ids from the Gemini API"
    )
    args = parser.parse_args(argv)
    try:
        settings = get_settings()
    except SettingsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.list_models:
        return _list_models(settings)
    return asyncio.run(_run(settings, args.task, args.prompt, args.stream))


if __name__ == "__main__":
    sys.exit(main())
