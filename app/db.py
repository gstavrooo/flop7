"""SQLAlchemy Core persistence for Flop7 (spec sections 3 and 7).

The full game state lives in one JSON column; there are no ORM models.
"""

from __future__ import annotations

import os
from typing import Any

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    event,
)
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

DEFAULT_DATABASE_URL = "sqlite:///flop7.db"

metadata = MetaData()

games = Table(
    "games",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("state", Text, nullable=False),
    Column("version", Integer, nullable=False, default=0),
    Column("winner", Integer, nullable=True),
    Column("created_at", DateTime, nullable=False),
)

seats = Table(
    "seats",
    metadata,
    Column("game_id", String(64), ForeignKey("games.id", ondelete="CASCADE"), primary_key=True),
    Column("seat", Integer, primary_key=True),
    Column("token_hash", String(64), nullable=False),
)

moves = Table(
    "moves",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("game_id", String(64), ForeignKey("games.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("seat", Integer, nullable=False),
    Column("action", String(16), nullable=False),
    Column("target", Integer, nullable=True),
    Column("created_at", DateTime, nullable=False),
)

TABLE_NAMES = ("games", "seats", "moves")


def get_database_url() -> str:
    """The database URL from the environment, with a local SQLite default."""
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


def create_db_engine(database_url: str | None = None) -> Engine:
    """Build an engine for SQLite, tuned for short concurrent web requests."""
    url = database_url or get_database_url()
    options: dict[str, Any] = {}
    in_memory = url.endswith(":memory:")
    if url.startswith("sqlite"):
        # FastAPI runs sync endpoints in a worker thread; the connection is still
        # guarded by the version-checked UPDATE, never shared as a mutable object.
        options["connect_args"] = {"check_same_thread": False, "timeout": 15}
        if in_memory:
            options["poolclass"] = StaticPool

    engine = create_engine(url, **options)

    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_connection: Any, _record: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=15000")
            if not in_memory:
                cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

    return engine
