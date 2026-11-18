from __future__ import annotations

from pathlib import Path

import pytest

from llm.config import MODELS_YAML, ModelsConfig, load_config
from llm.errors import LLMConfigError, UnknownTaskError

REQUIRED_TASKS = {"summary", "chat", "stt_cheap"}


def test_models_yaml_loads_and_tasks_resolve():
    cfg = load_config()
    assert set(cfg.tasks) >= REQUIRED_TASKS
    for name in cfg.tasks:
        task, model = cfg.resolve(name)
        assert model.litellm_model.count("/") >= 1
        assert model.model_id
        prices = model.prices_per_1m_usd
        assert prices.input_text > 0 and prices.output > 0
        assert prices.input_audio > 0
        assert 0 < prices.cached_input_text <= prices.input_text
        assert 0 < prices.cached_input_audio <= prices.input_audio
        assert task.timeout_s > 0


def test_unknown_task():
    with pytest.raises(UnknownTaskError):
        load_config().resolve("does-not-exist")


def test_task_pointing_to_missing_model_is_rejected():
    with pytest.raises(ValueError, match="undefined models"):
        ModelsConfig.model_validate({"tasks": {"chat": {"model": "ghost"}}, "models": {}})


def test_invalid_yaml_file(tmp_path: Path):
    bad = tmp_path / "models.yaml"
    bad.write_text("tasks: [unclosed", encoding="utf-8")
    with pytest.raises(LLMConfigError):
        load_config(bad)


def test_no_model_ids_in_python_sources():
    backend = MODELS_YAML.parents[1]
    needle = "gemini" + "-"  # split so this file does not match itself
    offenders = [
        str(p.relative_to(backend))
        for p in backend.rglob("*.py")
        if ".venv" not in p.parts and needle in p.read_text(encoding="utf-8", errors="ignore")
    ]
    assert offenders == []
