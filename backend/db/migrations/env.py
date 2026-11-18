"""Alembic environment. URL comes from core.settings unless the caller set
``sqlalchemy.url`` on the Config (tests do)."""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from db.models import Base

config = context.config

if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata

# Tables created and migrated by LangGraph's AsyncPostgresSaver.setup() (agent/checkpointer.py),
# not by Alembic: autogenerate must neither drop them nor report them as drift.
LANGGRAPH_TABLES = frozenset(
    {"checkpoints", "checkpoint_blobs", "checkpoint_writes", "checkpoint_migrations"}
)


def include_name(name: str | None, type_: str, parent_names: object) -> bool:
    if type_ == "table":
        return name not in LANGGRAPH_TABLES
    if type_ == "index":
        return not (
            isinstance(parent_names, dict) and parent_names.get("table_name") in LANGGRAPH_TABLES
        )
    return True


def _url() -> str:
    url = config.get_main_option("sqlalchemy.url")
    if url:
        return url
    from core.settings import get_settings

    return get_settings().database_url_sync_psycopg


def run_migrations_offline() -> None:
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_name=include_name,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        {"sqlalchemy.url": _url()},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata, include_name=include_name
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
