"""Importing app modules must never export the repo .env into os.environ.

``import litellm`` with LITELLM_MODE unset (it defaults to DEV) runs ``dotenv.load_dotenv()``;
``llm/__init__`` sets LITELLM_MODE=PRODUCTION first, so litellm must only be reached via
``llm`` (ruff TID251 enforces this outside ``llm/``). Each scenario runs in a fresh
subprocess with a scrubbed environment so the result does not depend on what this pytest
process already imported. The child reports only key *names*, never values.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from core.settings import ENV_FILE
from tests.conftest import BACKEND_DIR

ALWAYS_WATCHED = {"GEMINI_API_KEY", "LANGSMITH_API_KEY", "LANGSMITH_TRACING", "LANGSMITH_PROJECT"}


def _dotenv_keys() -> set[str]:
    """Variable names (not values) defined in the repo .env, if it exists."""
    if not ENV_FILE.exists():
        return set()
    names: set[str] = set()
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip().removeprefix("export ").strip()
        if line and not line.startswith("#") and "=" in line:
            names.add(line.split("=", 1)[0].strip())
    return names


WATCHED = ALWAYS_WATCHED | _dotenv_keys()

CHILD = """
import importlib, json, os, sys
watched = json.loads(sys.argv[1])
for mod in json.loads(sys.argv[2]):
    importlib.import_module(mod)
leaked = sorted(k for k in os.environ if k in watched or k.startswith("LANGSMITH_"))
print(json.dumps({"leaked": leaked, "litellm_mode": os.environ.get("LITELLM_MODE")}))
"""


def _import_in_fresh_process(modules: list[str]) -> dict[str, object]:
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in WATCHED and not k.startswith(("LANGSMITH_", "LITELLM_"))
    }
    proc = subprocess.run(
        [sys.executable, "-c", CHILD, json.dumps(sorted(WATCHED)), json.dumps(modules)],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize(
    "modules",
    [
        # agent + checkpointer (langgraph/langsmith/psycopg) before llm, then litellm via llm
        ["agent.chat_graph", "agent.checkpointer", "llm", "llm.fake", "llm.gateway"],
        ["agent.checkpointer", "langgraph.checkpoint.postgres", "langsmith", "llm.fake"],
        ["agent.answer_graph"],
        ["api.main"],
    ],
    ids=["chat_graph+checkpointer+llm", "checkpointer-first", "answer_graph", "api.main"],
)
def test_imports_do_not_export_dotenv(modules: list[str]) -> None:
    result = _import_in_fresh_process(modules)
    assert result["leaked"] == []
    assert result["litellm_mode"] == "PRODUCTION"


def test_bare_litellm_import_would_leak() -> None:
    """Control: proves the probe above detects the leak (needs a .env defining a key)."""
    if not _dotenv_keys():
        pytest.skip("no repo .env with variables to detect")
    result = _import_in_fresh_process(["litellm"])
    assert result["leaked"], "bare `import litellm` no longer loads .env; guard may be obsolete"
