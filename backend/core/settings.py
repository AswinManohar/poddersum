"""Typed application settings for the PodderSum backend.

Values come from real environment variables first, then the repo-root ``.env``
(resolved relative to this file, not the current working directory).

Construct settings in tests without touching the real ``.env``::

    Settings(_env_file=None, GEMINI_API_KEY="x")
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = REPO_ROOT / ".env"

LOCAL_DATABASE_URL = "postgresql+asyncpg://poddersum:poddersum@localhost:5433/poddersum"

AppEnv = Literal["local", "staging", "production"]

_POSTGRES_SCHEMES = {
    "postgres",
    "postgresql",
    "postgresql+asyncpg",
    "postgresql+psycopg",
    "postgresql+psycopg2",
    "postgresql+pg8000",
}


class SettingsError(RuntimeError):
    """Raised when configuration is missing or invalid. Names variables, never values."""


def _with_scheme(url: str, scheme: str) -> str:
    """Return ``url`` with its scheme replaced by ``scheme`` (any Postgres flavour accepted)."""
    current, sep, rest = url.partition("://")
    if not sep or current.lower() not in _POSTGRES_SCHEMES:
        raise ValueError("DATABASE_URL must be a postgres:// or postgresql[+driver]:// URL")
    return f"{scheme}://{rest}"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    # Runtime
    APP_ENV: AppEnv = "local"

    # LLM provider (required)
    GEMINI_API_KEY: SecretStr

    # Database: defaults to the docker Postgres when APP_ENV=local, required otherwise.
    DATABASE_URL: str | None = Field(default=None, repr=False)

    # LangSmith tracing (optional)
    LANGSMITH_TRACING: bool = False
    LANGSMITH_API_KEY: SecretStr | None = None
    LANGSMITH_PROJECT: str | None = None
    LANGSMITH_ENDPOINT: str | None = None

    # HTTP
    CORS_ORIGINS: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000", "http://127.0.0.1:3000"]
    )

    def __init__(self, **values: Any) -> None:
        try:
            super().__init__(**values)
        except ValidationError as exc:
            raise SettingsError(_describe(exc)) from None

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def _split_origins(cls, value: Any) -> Any:
        if isinstance(value, str):
            return [o.strip() for o in value.split(",") if o.strip()]
        return value

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def _blank_is_none(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def _database_url_required_outside_local(self) -> Settings:
        if self.DATABASE_URL is None:
            if self.APP_ENV != "local":
                raise ValueError(f"DATABASE_URL is required when APP_ENV={self.APP_ENV}")
            self.DATABASE_URL = LOCAL_DATABASE_URL
        # Validate the scheme early so a bad URL fails at boot.
        _with_scheme(self.DATABASE_URL, "postgresql")
        return self

    @property
    def database_url_async(self) -> str:
        """SQLAlchemy async URL (asyncpg driver)."""
        return _with_scheme(self._db_url, "postgresql+asyncpg")

    @property
    def database_url_sync_psycopg(self) -> str:
        """SQLAlchemy sync URL with the psycopg3 driver (Alembic)."""
        return _with_scheme(self._db_url, "postgresql+psycopg")

    @property
    def database_url_libpq(self) -> str:
        """Plain libpq ``postgresql://`` URL (psycopg3 directly, LangGraph checkpointer)."""
        return _with_scheme(self._db_url, "postgresql")

    @property
    def _db_url(self) -> str:
        assert self.DATABASE_URL is not None  # set by the model validator
        return self.DATABASE_URL


def _describe(exc: ValidationError) -> str:
    """Summarise a ValidationError by variable name only (input values are never included)."""
    missing: list[str] = []
    invalid: list[str] = []
    for err in exc.errors(include_input=False, include_url=False):
        name = ".".join(str(p) for p in err["loc"])
        msg = err["msg"].removeprefix("Value error, ")
        if err["type"] == "missing":
            missing.append(name)
        elif name:
            invalid.append(f"{name} ({msg})")
        else:
            invalid.append(msg)
    parts = []
    if missing:
        parts.append("missing required environment variable(s): " + ", ".join(missing))
    if invalid:
        parts.append("invalid configuration: " + "; ".join(invalid))
    return (
        "PodderSum settings error: "
        + "; ".join(parts)
        + f". Set them in the environment or {ENV_FILE} (template: .env.example)."
    )


@lru_cache
def get_settings() -> Settings:
    """Process-wide settings, loaded once."""
    return Settings()
