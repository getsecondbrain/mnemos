from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

from app.config import get_settings


@event.listens_for(Engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


engine = create_engine(
    get_settings().db_url,
    echo=False,
    connect_args={"check_same_thread": False},
)


def create_db_and_tables() -> None:
    SQLModel.metadata.create_all(engine)
    _run_migrations(engine)


def _run_migrations(eng) -> None:
    """Lightweight forward-only migrations for schema additions."""
    from sqlalchemy import inspect, text

    insp = inspect(eng)
    columns = [c["name"] for c in insp.get_columns("memories")]
    if "source_id" not in columns:
        with eng.begin() as conn:
            conn.execute(
                text("ALTER TABLE memories ADD COLUMN source_id TEXT REFERENCES sources(id)")
            )
    if "visibility" not in columns:
        with eng.begin() as conn:
            conn.execute(
                text("ALTER TABLE memories ADD COLUMN visibility TEXT DEFAULT 'public'")
            )

    # Location fields (P12.1)
    for col_name, col_def in [
        ("latitude", "REAL"),
        ("longitude", "REAL"),
        ("place_name", "TEXT"),
        ("place_name_dek", "TEXT"),
    ]:
        if col_name not in columns:
            with eng.begin() as conn:
                conn.execute(
                    text(f"ALTER TABLE memories ADD COLUMN {col_name} {col_def}")
                )


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session
