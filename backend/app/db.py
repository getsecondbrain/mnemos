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

    # Soft delete (deleted_at)
    if "deleted_at" not in columns:
        with eng.begin() as conn:
            conn.execute(text("ALTER TABLE memories ADD COLUMN deleted_at TEXT"))

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

    # Conversation messages table
    if "conversation_messages" not in insp.get_table_names():
        with eng.begin() as conn:
            conn.execute(text(
                "CREATE TABLE conversation_messages ("
                "  id TEXT PRIMARY KEY,"
                "  conversation_id TEXT NOT NULL REFERENCES conversations(id),"
                "  role TEXT NOT NULL,"
                "  content TEXT NOT NULL,"
                "  sources TEXT,"
                "  created_at TEXT NOT NULL"
                ")"
            ))
            conn.execute(text(
                "CREATE INDEX ix_conversation_messages_conversation_id "
                "ON conversation_messages(conversation_id)"
            ))

    # Person model extensions (A1.2)
    person_cols = [c["name"] for c in insp.get_columns("persons")]
    for col_name, col_def in [
        ("relationship_to_owner", "TEXT"),
        ("is_deceased", "INTEGER DEFAULT 0"),
        ("gedcom_id", "TEXT"),
    ]:
        if col_name not in person_cols:
            with eng.begin() as conn:
                conn.execute(
                    text(f"ALTER TABLE persons ADD COLUMN {col_name} {col_def}")
                )


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session
