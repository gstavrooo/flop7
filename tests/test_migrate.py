"""Migrations: initial schema, round-trip, boot apply and legacy refusal (spec 7/8)."""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect

from app.db import TABLE_NAMES
from app.main import create_app
from app.migrate import apply_migrations, downgrade_to_base


def db_url(tmp_path) -> str:
    return f"sqlite:///{tmp_path / 'flop7.db'}"


def table_names(url: str) -> set[str]:
    engine = create_engine(url)
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_initial_migration_creates_the_three_tables(tmp_path):
    url = db_url(tmp_path)
    apply_migrations(url)

    assert set(TABLE_NAMES) <= table_names(url)
    assert "alembic_version" in table_names(url)

    engine = create_engine(url)
    try:
        inspector = inspect(engine)
        assert {column["name"] for column in inspector.get_columns("games")} == {
            "id",
            "state",
            "version",
            "winner",
            "created_at",
        }
        assert {column["name"] for column in inspector.get_columns("seats")} == {
            "game_id",
            "seat",
            "token_hash",
        }
        assert {column["name"] for column in inspector.get_columns("moves")} == {
            "id",
            "game_id",
            "seat",
            "action",
            "target",
            "created_at",
        }
        assert inspector.get_pk_constraint("seats")["constrained_columns"] == ["game_id", "seat"]
        assert [index["name"] for index in inspector.get_indexes("moves")] == ["ix_moves_game_id"]
    finally:
        engine.dispose()


def test_migrations_are_idempotent(tmp_path):
    url = db_url(tmp_path)
    apply_migrations(url)
    apply_migrations(url)
    assert set(TABLE_NAMES) <= table_names(url)


def test_downgrade_then_upgrade_round_trip(tmp_path):
    url = db_url(tmp_path)
    apply_migrations(url)

    downgrade_to_base(url)
    assert not set(TABLE_NAMES).intersection(table_names(url))

    apply_migrations(url)
    assert set(TABLE_NAMES) <= table_names(url)


def test_legacy_database_is_refused_loudly(tmp_path):
    url = db_url(tmp_path)
    path = tmp_path / "flop7.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE games (id TEXT PRIMARY KEY, state TEXT, version INTEGER)")

    with pytest.raises(RuntimeError, match="legacy database"):
        apply_migrations(url)

    # Refusing must not half-migrate or destroy the old schema.
    assert table_names(url) == {"games"}


def test_boot_migration_when_enabled(tmp_path):
    url = db_url(tmp_path)
    app = create_app(database_url=url, migrate_on_start=True)
    with TestClient(app) as client:
        assert client.get("/api/health").json() == {"ok": True}
    assert set(TABLE_NAMES) <= table_names(url)


def test_no_boot_migration_when_disabled(tmp_path):
    url = db_url(tmp_path)
    app = create_app(database_url=url, migrate_on_start=False)
    with TestClient(app) as client:
        assert client.get("/api/health").json() == {"ok": True}
    assert not set(TABLE_NAMES).intersection(table_names(url))
