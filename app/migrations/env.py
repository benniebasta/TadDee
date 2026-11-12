"""Alembic environment for Nest. Used two ways:

- by the app at start (app/migrate.py passes an open connection in
  config.attributes["connection"]), and
- by hand: `alembic -c app/migrations/alembic.ini revision -m "..."`
  (the database is then <NEST_DATA_DIR>/nest.db, default ./data/nest.db).
"""
from __future__ import annotations

import os

from alembic import context
from sqlalchemy import create_engine

from app import models  # noqa: F401  (registers every table on Base)
from app.db import Base, default_data_dir

target_metadata = Base.metadata


def _run(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata,
                      render_as_batch=True,          # SQLite: ALTER through table copies
                      compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    conn = context.config.attributes.get("connection")
    if conn is not None:
        _run(conn)
        return
    url = context.config.get_main_option("sqlalchemy.url") or \
        "sqlite:///" + os.path.join(default_data_dir(), "nest.db")
    engine = create_engine(url)
    with engine.connect() as connection:
        _run(connection)
    engine.dispose()


if context.is_offline_mode():
    context.configure(url=context.config.get_main_option("sqlalchemy.url") or "sqlite://",
                      target_metadata=target_metadata, literal_binds=True, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    run_migrations_online()
