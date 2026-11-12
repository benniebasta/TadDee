# Database migrations

The app runs `upgrade heads` itself every time it opens a data folder
(`app/migrate.py`). A database made before migrations existed is stamped at
`0001` (the v1 schema) first. Old jobs are kept.

## Add a migration
1. Change the model in `app/models.py`.
2. Make the file, from the repo root, with the next free number:

       alembic -c app/migrations/alembic.ini revision -m "add feeds table" --rev-id 0003

   or copy `versions/0002_platform.py`. Name: `NNNN_<what>.py`, `revision = "NNNN"`,
   `down_revision` = the newest number before it.
3. Write `upgrade()` by hand (autogenerate also works:
   `... revision --autogenerate ...` against a database at the previous revision).
   SQLite cannot ALTER most things: use `with op.batch_alter_table("jobs") as b:`.
   New columns on existing tables must be `nullable=True` or have a `server_default`,
   so old rows stay valid.
4. `python -m pytest -q tests/test_platform.py` checks that the migrated schema
   equals the models (no missing column, no forgotten migration).

Never edit a migration that was already released; add a new one.
Parallel branches that both added `0003`: renumber one of them to `0004` with
`down_revision = "0003"` when merging (the app would still apply both, because it
upgrades to all heads, but keep the chain straight).
