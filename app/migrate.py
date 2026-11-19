"""Boot-time Alembic upgrade with a loud refusal for legacy, unmigrated DBs."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from app.db import TABLE_NAMES, create_db_engine, get_database_url

ALEMBIC_INI = Path(__file__).resolve().parent / "migrations" / "alembic.ini"
LEGACY_HINT = (
    "legacy database detected: schema tables exist without an Alembic baseline. "
    "Verify the old schema, then stamp a migration baseline (alembic stamp head) "
    "and create a real migration for the differences — refusing to guess."
)


def alembic_config(database_url: str | None = None) -> Config:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("sqlalchemy.url", database_url or get_database_url())
    return config


def _assert_not_legacy(engine: Engine) -> None:
    """A pre-existing schema without alembic_version must never be auto-migrated."""
    table_names = set(inspect(engine).get_table_names())
    if not table_names.intersection(TABLE_NAMES):
        return  # fresh database: the initial migration will create everything
    if "alembic_version" not in table_names:
        raise RuntimeError(LEGACY_HINT)
    with engine.connect() as connection:
        if connection.execute(text("SELECT version_num FROM alembic_version LIMIT 1")).first() is None:
            raise RuntimeError(LEGACY_HINT)


def apply_migrations(database_url: str | None = None) -> None:
    url = database_url or get_database_url()
    engine = create_db_engine(url)
    try:
        _assert_not_legacy(engine)
        command.upgrade(alembic_config(url), "head")
    finally:
        engine.dispose()


def downgrade_to_base(database_url: str | None = None) -> None:
    """The inverse migration path (used by the round-trip test)."""
    command.downgrade(alembic_config(database_url), "base")
