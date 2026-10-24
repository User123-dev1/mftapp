"""Database engine and session handling."""

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


def _make_engine(url: str):
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, connect_args=connect_args)


engine = _make_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def configure(url: str) -> None:
    """Point the app at a different database (used by tests)."""
    global engine
    engine = _make_engine(url)
    SessionLocal.configure(bind=engine)


def init_db() -> None:
    from . import models  # noqa: F401  (register models)

    Base.metadata.create_all(bind=engine)
    _add_missing_columns()


# Columns added after the first release; create_all() does not alter existing tables.
_NEW_COLUMNS = {
    "users": {"sso_provider": "VARCHAR(50)", "sso_subject": "VARCHAR(255)"},
    "tickets": {"ci_id": "INTEGER REFERENCES config_items(id)"},
    "support_groups": {"auto_assign": "BOOLEAN NOT NULL DEFAULT 0"},
    "config_items": {
        "entra_object_id": "VARCHAR(64)",
        "entra_last_sign_in": "DATETIME",
        "entra_synced_at": "DATETIME",
    },
}


# SQLite can't add a UNIQUE column to an existing table, so enforce it with an index.
_NEW_UNIQUE_INDEXES = {
    "uq_config_items_entra_object_id": ("config_items", "entra_object_id"),
}


def _add_missing_columns() -> None:
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table, columns in _NEW_COLUMNS.items():
            existing = {c["name"] for c in inspector.get_columns(table)}
            for name, ddl in columns.items():
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
        for index, (table, column) in _NEW_UNIQUE_INDEXES.items():
            conn.execute(text(f"CREATE UNIQUE INDEX IF NOT EXISTS {index} ON {table} ({column})"))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
