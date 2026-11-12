"""Platform: background tasks table; jobs.settings_json (duplicate copies
the nest settings) and jobs.deleted_at (30-day bin).

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-12
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("jobs") as b:
        b.add_column(sa.Column("settings_json", sa.Text(), nullable=True))
        b.add_column(sa.Column("deleted_at", sa.DateTime(), nullable=True))
        b.create_index("ix_jobs_deleted_at", ["deleted_at"])
    op.create_table(
        "tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=True),
        sa.Column("ref", sa.String(32), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("progress", sa.Float(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("params_json", sa.Text(), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_tasks_kind", "tasks", ["kind"])
    op.create_index("ix_tasks_job_id", "tasks", ["job_id"])
    op.create_index("ix_tasks_status", "tasks", ["status"])


def downgrade() -> None:
    op.drop_table("tasks")
    with op.batch_alter_table("jobs") as b:
        b.drop_index("ix_jobs_deleted_at")
        b.drop_column("deleted_at")
        b.drop_column("settings_json")
