"""Typed loader for ``llm/models.yaml``, the only place model ids and prices live."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from llm.errors import LLMConfigError, UnknownTaskError

MODELS_YAML = Path(__file__).with_name("models.yaml")


class ModelPrices(BaseModel):
    """USD per 1M tokens."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    input_text: float = Field(ge=0)
    input_audio: float = Field(ge=0)
    output: float = Field(ge=0)
    cached_input_text: float = Field(ge=0)
    cached_input_audio: float = Field(ge=0)


class ModelSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    litellm_model: str
    provider: str
    prices_per_1m_usd: ModelPrices
    context_window: int | None = None
    max_output_tokens: int | None = None

    @property
    def model_id(self) -> str:
        """Provider model id (the part after ``<provider>/``)."""
        return self.litellm_model.split("/", 1)[-1]


class TaskSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model: str
    params: dict[str, Any] = Field(default_factory=dict)
    timeout_s: float = 120
    max_retries: int = Field(default=2, ge=0, le=5)


class ModelsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tasks: dict[str, TaskSpec]
    models: dict[str, ModelSpec]

    @model_validator(mode="after")
    def _tasks_point_to_models(self) -> ModelsConfig:
        missing = {t: s.model for t, s in self.tasks.items() if s.model not in self.models}
        if missing:
            raise ValueError(f"tasks reference undefined models: {missing}")
        return self

    def task(self, task: str) -> TaskSpec:
        try:
            return self.tasks[task]
        except KeyError:
            raise UnknownTaskError(
                f"unknown LLM task {task!r}; defined tasks: {sorted(self.tasks)}"
            ) from None

    def resolve(self, task: str) -> tuple[TaskSpec, ModelSpec]:
        spec = self.task(task)
        return spec, self.models[spec.model]


def load_config(path: Path | str = MODELS_YAML) -> ModelsConfig:
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        return ModelsConfig.model_validate(data)
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise LLMConfigError(f"invalid model config {path}: {exc}") from exc


@lru_cache
def get_config() -> ModelsConfig:
    return load_config()
