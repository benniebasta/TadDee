"""Production features: feeds and speeds library, ramp entry per bit,
material grain, part mirror and colour, offcut name.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-13
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("tools") as b:
        b.add_column(sa.Column("ramp_mm", sa.Float(), nullable=False, server_default="12"))
    with op.batch_alter_table("materials") as b:
        b.add_column(sa.Column("grain", sa.Boolean(), nullable=False, server_default="0"))
    with op.batch_alter_table("job_parts") as b:
        b.add_column(sa.Column("mirror", sa.Boolean(), nullable=False, server_default="0"))
        b.add_column(sa.Column("color", sa.String(16), nullable=False, server_default=""))
    with op.batch_alter_table("offcuts") as b:
        b.add_column(sa.Column("name", sa.String(200), nullable=False, server_default=""))
    op.create_table(
        "feeds",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("material_id", sa.Integer(), sa.ForeignKey("materials.id"), nullable=False),
        sa.Column("tool_id", sa.Integer(), sa.ForeignKey("tools.id"), nullable=False),
        sa.Column("feed_mm_min", sa.Float(), nullable=True),
        sa.Column("plunge_mm_min", sa.Float(), nullable=True),
        sa.Column("rpm", sa.Integer(), nullable=True),
        sa.Column("pass_depth_mm", sa.Float(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.UniqueConstraint("material_id", "tool_id", name="uq_feeds_material_tool"),
    )
    op.create_index("ix_feeds_material_id", "feeds", ["material_id"])
    op.create_index("ix_feeds_tool_id", "feeds", ["tool_id"])


def downgrade() -> None:
    op.drop_table("feeds")
    with op.batch_alter_table("offcuts") as b:
        b.drop_column("name")
    with op.batch_alter_table("job_parts") as b:
        b.drop_column("color")
        b.drop_column("mirror")
    with op.batch_alter_table("materials") as b:
        b.drop_column("grain")
    with op.batch_alter_table("tools") as b:
        b.drop_column("ramp_mm")
