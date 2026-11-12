"""Database setup: one SQLite file per data folder (<DATA_DIR>/nest.db).

The data folder is chosen by create_app(data_dir) or the NEST_DATA_DIR
environment variable (default ./data). Back up the app by copying the folder.
"""
from __future__ import annotations

import os
import threading
from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    """Parent class of every table in app/models.py."""


def default_data_dir() -> str:
    return os.path.abspath(os.environ.get("NEST_DATA_DIR") or "./data")


class Database:
    """Engine + session factory for one data folder."""

    def __init__(self, data_dir: str):
        self.data_dir = os.path.abspath(data_dir)
        os.makedirs(self.data_dir, exist_ok=True)
        self.path = os.path.join(self.data_dir, "nest.db")
        # check_same_thread=False: FastAPI runs plain `def` endpoints in a
        # thread pool, so one connection may be used from different threads
        # (never at the same time: each request gets its own session).
        self.engine: Engine = create_engine(f"sqlite:///{self.path}",
                                            connect_args={"check_same_thread": False, "timeout": 30})
        event.listen(self.engine, "connect", _sqlite_pragmas)
        self.SessionLocal = sessionmaker(self.engine, expire_on_commit=False)

    def create_all(self) -> None:
        from . import models  # noqa: F401  (registers the tables on Base)
        Base.metadata.create_all(self.engine)
        add_missing_columns(self.engine)

    def session(self) -> Session:
        return self.SessionLocal()

    def dispose(self) -> None:
        self.engine.dispose()


def add_missing_columns(engine: Engine) -> None:
    """Older data folders lack columns added later (materials.grain,
    job_parts.mirror/color, offcuts.name): add them with their server default
    so old jobs keep working. Only adds, never drops or changes."""
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have:
                    continue
                default = col.server_default.arg if col.server_default is not None else None
                ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {col.type.compile(engine.dialect)}'
                if default is not None:
                    ddl += f" DEFAULT '{default}'" if not str(default).isdigit() else f" DEFAULT {default}"
                conn.execute(text(ddl))


def _sqlite_pragmas(dbapi_conn, _record) -> None:
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")   # readers do not block the writer
    cur.execute("PRAGMA foreign_keys=OFF")   # we clean up links ourselves
    cur.close()


_databases: dict[str, Database] = {}
_lock = threading.Lock()


def get_database(data_dir: str | None = None) -> Database:
    """One Database per data folder, created on first use (tables included)."""
    d = os.path.abspath(data_dir or default_data_dir())
    with _lock:
        db = _databases.get(d)
        if db is None:
            db = Database(d)
            db.create_all()
            _databases[d] = db
        return db


def session_scope(db: Database) -> Iterator[Session]:
    """FastAPI dependency body: a session that is closed after the request."""
    s = db.session()
    try:
        yield s
    finally:
        s.close()
