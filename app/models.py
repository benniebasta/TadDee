"""SQLAlchemy tables (PLAN.md section 4.7). Geometry is stored as WKT text,
results as JSON text. Ids are integers except relief runs (short hex)."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Machine(Base):
    __tablename__ = "machines"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(10), default="cnc")      # cnc | laser
    bed_w: Mapped[float] = mapped_column(Float, default=0.0)
    bed_h: Mapped[float] = mapped_column(Float, default=0.0)
    rate_per_hour: Mapped[float] = mapped_column(Float, default=0.0)  # THB per hour, excl. VAT
    notes: Mapped[str] = mapped_column(Text, default="")


class Tool(Base):
    __tablename__ = "tools"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    diameter_mm: Mapped[float] = mapped_column(Float)
    kind: Mapped[str] = mapped_column(String(10), default="flat")     # flat | ball | vbit
    angle_deg: Mapped[float | None] = mapped_column(Float, nullable=True)
    feed_mm_min: Mapped[float] = mapped_column(Float, default=3000.0)
    plunge_mm_min: Mapped[float] = mapped_column(Float, default=800.0)
    rpm: Mapped[int] = mapped_column(Integer, default=18000)
    pass_depth_mm: Mapped[float] = mapped_column(Float, default=3.0)
    ramp_mm: Mapped[float] = mapped_column(Float, default=12.0, server_default="12")  # 0 = straight plunge


class Material(Base):
    __tablename__ = "materials"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    thickness_mm: Mapped[float] = mapped_column(Float)
    sheet_w: Mapped[float] = mapped_column(Float)
    sheet_h: Mapped[float] = mapped_column(Float)
    price_per_sheet: Mapped[float] = mapped_column(Float, default=0.0)  # THB, excl. VAT; 0 = not set
    margin_mm: Mapped[float] = mapped_column(Float, default=10.0)


class FeedOverride(Base):
    """Feeds and speeds library: what one bit runs at in one material. Every
    value is optional; None = use the bit's own default."""
    __tablename__ = "feeds"
    __table_args__ = (UniqueConstraint("material_id", "tool_id", name="uq_feeds_material_tool"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id"), index=True)
    tool_id: Mapped[int] = mapped_column(ForeignKey("tools.id"), index=True)
    feed_mm_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    plunge_mm_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    rpm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pass_depth_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    material_id: Mapped[int | None] = mapped_column(ForeignKey("materials.id"), nullable=True)
    machine_id: Mapped[int | None] = mapped_column(ForeignKey("machines.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft|nested|exported|cut
    exports_json: Mapped[str] = mapped_column(Text, default="[]")     # list of file records
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    # last nest settings, kept apart from the result so a duplicate can copy them (migration 0002)
    settings_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # set = in the bin (soft delete); purged 30 days later (migration 0002)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)


class JobPart(Base):
    __tablename__ = "job_parts"
    pk: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    part_id: Mapped[str] = mapped_column(String(32))     # Part.id from the importer
    position: Mapped[int] = mapped_column(Integer, default=0)
    name: Mapped[str] = mapped_column(String(200))
    qty: Mapped[int] = mapped_column(Integer, default=1)
    shape_wkt: Mapped[str] = mapped_column(Text)
    marks_wkt: Mapped[str] = mapped_column(Text, default="[]")  # JSON list of LINESTRING WKT
    mark_layer: Mapped[str] = mapped_column(String(10), default="engrave")
    source: Mapped[str] = mapped_column(String(300), default="")


class NestRun(Base):
    __tablename__ = "nest_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    settings_json: Mapped[str] = mapped_column(Text)
    result_json: Mapped[str] = mapped_column(Text)   # {"result": raw NestResult, "out": NestOut}
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


class Offcut(Base):
    __tablename__ = "offcuts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    material_id: Mapped[int | None] = mapped_column(ForeignKey("materials.id"), nullable=True, index=True)
    width: Mapped[float] = mapped_column(Float)
    height: Mapped[float] = mapped_column(Float)
    outline_wkt: Mapped[str] = mapped_column(Text)   # in its own frame, bottom-left at (0,0)
    source_job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    used: Mapped[bool] = mapped_column(Boolean, default=False)


class ReliefRun(Base):
    __tablename__ = "relief_runs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    image_path: Mapped[str] = mapped_column(Text)
    params_json: Mapped[str] = mapped_column(Text)
    result_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


class Task(Base):
    """Background work (nest or relief) run by app/tasks.py, one at a time per kind."""
    __tablename__ = "tasks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), index=True)          # nest | relief
    job_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    ref: Mapped[str | None] = mapped_column(String(32), nullable=True)  # relief run id
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    # queued | running | done | failed | cancelled
    progress: Mapped[float] = mapped_column(Float, default=0.0)        # 0..1
    message: Mapped[str] = mapped_column(Text, default="")
    params_json: Mapped[str] = mapped_column(Text, default="{}")
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
