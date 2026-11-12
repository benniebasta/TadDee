"""Baseline: the v1 schema (machines, tools, materials, jobs, job_parts,
nest_runs, offcuts, relief_runs) exactly as Base.metadata.create_all made it
before migrations existed. Databases made before Alembic are stamped at this
revision (see app/migrate.py), so never change this file.

Revision ID: 0001
Revises:
Create Date: 2026-11-12
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "machines",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("bed_w", sa.Float(), nullable=False),
        sa.Column("bed_h", sa.Float(), nullable=False),
        sa.Column("rate_per_hour", sa.Float(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
    )
    op.create_table(
        "tools",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("diameter_mm", sa.Float(), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("angle_deg", sa.Float(), nullable=True),
        sa.Column("feed_mm_min", sa.Float(), nullable=False),
        sa.Column("plunge_mm_min", sa.Float(), nullable=False),
        sa.Column("rpm", sa.Integer(), nullable=False),
        sa.Column("pass_depth_mm", sa.Float(), nullable=False),
    )
    op.create_table(
        "materials",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("thickness_mm", sa.Float(), nullable=False),
        sa.Column("sheet_w", sa.Float(), nullable=False),
        sa.Column("sheet_h", sa.Float(), nullable=False),
        sa.Column("price_per_sheet", sa.Float(), nullable=False),
        sa.Column("margin_mm", sa.Float(), nullable=False),
    )
    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("material_id", sa.Integer(), sa.ForeignKey("materials.id"), nullable=True),
        sa.Column("machine_id", sa.Integer(), sa.ForeignKey("machines.id"), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("exports_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "job_parts",
        sa.Column("pk", sa.Integer(), primary_key=True),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column("part_id", sa.String(32), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("shape_wkt", sa.Text(), nullable=False),
        sa.Column("marks_wkt", sa.Text(), nullable=False),
        sa.Column("mark_layer", sa.String(10), nullable=False),
        sa.Column("source", sa.String(300), nullable=False),
    )
    op.create_index("ix_job_parts_job_id", "job_parts", ["job_id"])
    op.create_table(
        "nest_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column("settings_json", sa.Text(), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_nest_runs_job_id", "nest_runs", ["job_id"])
    op.create_table(
        "offcuts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("material_id", sa.Integer(), sa.ForeignKey("materials.id"), nullable=True),
        sa.Column("width", sa.Float(), nullable=False),
        sa.Column("height", sa.Float(), nullable=False),
        sa.Column("outline_wkt", sa.Text(), nullable=False),
        sa.Column("source_job_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("used", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_offcuts_material_id", "offcuts", ["material_id"])
    op.create_table(
        "relief_runs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("image_path", sa.Text(), nullable=False),
        sa.Column("params_json", sa.Text(), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    for name in ("relief_runs", "offcuts", "nest_runs", "job_parts", "jobs", "materials", "tools", "machines"):
        op.drop_table(name)
