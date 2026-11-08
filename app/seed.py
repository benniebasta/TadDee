"""Starting data, written only when a table is empty. Prices and machine
rates are 0 on purpose: staff fill in real numbers (never invent prices)."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Machine, Material, Tool

MACHINES = [
    dict(name="Router (NC Studio)", kind="cnc", bed_w=2500, bed_h=1300, rate_per_hour=0, notes=""),
    dict(name="Laser (Ruida)", kind="laser", bed_w=1300, bed_h=900, rate_per_hour=0, notes=""),
]
TOOLS = [
    dict(name="6 mm flat 2-flute", diameter_mm=6, kind="flat", angle_deg=None, pass_depth_mm=3),
    dict(name="3 mm flat", diameter_mm=3, kind="flat", angle_deg=None, pass_depth_mm=2),
    dict(name="3 mm ball", diameter_mm=3, kind="ball", angle_deg=None, pass_depth_mm=1.5),
    dict(name="60° V-bit", diameter_mm=6, kind="vbit", angle_deg=60, pass_depth_mm=1),
]
MATERIALS = [
    dict(name="Acrylic 3 mm", thickness_mm=3, sheet_w=2440, sheet_h=1220, price_per_sheet=0, margin_mm=10),
    dict(name="ACP 3 mm", thickness_mm=3, sheet_w=2440, sheet_h=1220, price_per_sheet=0, margin_mm=10),
    dict(name="MDF 18 mm", thickness_mm=18, sheet_w=2440, sheet_h=1220, price_per_sheet=0, margin_mm=10),
    dict(name="PVC foam 5 mm", thickness_mm=5, sheet_w=2440, sheet_h=1220, price_per_sheet=0, margin_mm=10),
]


def seed(session: Session) -> None:
    for model, rows in ((Machine, MACHINES), (Tool, TOOLS), (Material, MATERIALS)):
        if session.scalar(select(func.count()).select_from(model)) == 0:
            session.add_all(model(**r) for r in rows)
    session.commit()
