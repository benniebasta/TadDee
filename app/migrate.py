"""Schema migrations (Alembic), run automatically when a data folder is opened.

- New database: every migration runs from 0001.
- Database made before migrations existed (tables but no alembic_version):
  stamped at the baseline 0001, then upgraded.
- Otherwise: upgraded to the newest revision ("heads", so two branches that
  each added a migration still both apply after a merge).

How to add a migration: see app/migrations/README.md.
"""
from __future__ import annotations

import logging
import os

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect
from sqlalchemy.engine import Engine

MIGRATIONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "migrations")
BASELINE = "0001"
log = logging.getLogger("nest.migrate")


def alembic_config() -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", MIGRATIONS_DIR)
    return cfg


def head_revisions() -> list[str]:
    return list(ScriptDirectory.from_config(alembic_config()).get_heads())


def current_revisions(engine: Engine) -> list[str]:
    from alembic.runtime.migration import MigrationContext
    with engine.connect() as conn:
        return list(MigrationContext.configure(conn).get_current_heads())


def migrate(engine: Engine) -> None:
    cfg = alembic_config()
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        tables = set(inspect(conn).get_table_names())
        if "alembic_version" not in tables and "jobs" in tables:
            log.info("existing database without a schema version: stamping baseline %s", BASELINE)
            command.stamp(cfg, BASELINE)
        command.upgrade(cfg, "heads")
