"""Glue between the HTTP routes, the database and the geometry modules.

The geometry modules (core.importers, core.svgout, nesting.engine, cam.*,
relief.api) are imported inside the functions that use them, so the app
still starts when one of them is missing or broken; only the feature that
needs it fails, with a plain message.
"""
from __future__ import annotations

import io
import json
import math
import os
import re
import shutil
import time
import uuid
import zipfile
from dataclasses import asdict, fields

from shapely import wkt as shapely_wkt
from shapely.affinity import translate
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.models import NestResult, NestSettings, Part, Placement, SheetResult, SheetSpec

from .models import Job, JobPart, Machine, Material, NestRun, Offcut, ReliefRun, Tool

VERSION = "0.1.0"
MAX_UPLOAD = 20 * 1024 * 1024          # 20 MB per file
MAX_TIME_LIMIT = 60.0
DEFAULT_ROTATIONS = [0.0, 90.0, 180.0, 270.0]
DEFAULT_CUT_THROUGH = 0.3


class UserError(Exception):
    """Something the user can fix. Becomes an HTTP error with this sentence."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def not_found(what: str) -> UserError:
    return UserError(f"{what} not found", 404)


# ---------- small helpers ----------

def iso(dt) -> str:
    return dt.replace(microsecond=0).isoformat() + "Z" if dt else ""


def slug(text: str, fallback: str) -> str:
    """Safe piece of a file name: letters, digits, '-' and '_' only."""
    s = re.sub(r"[^A-Za-z0-9_-]+", "-", text or "").strip("-_")
    return (s[:40] or fallback)


def is_safe_name(name: str) -> bool:
    return bool(name) and "/" not in name and "\\" not in name and ".." not in name \
        and not name.startswith(".") and "\x00" not in name


def safe_file(folder: str, name: str) -> str:
    """Full path of `name` inside `folder`, only if it is a plain file name
    that really exists there. Anything else is a 404 (or 400 for bad names)."""
    if not is_safe_name(name):
        raise UserError("Bad file name", 400)
    if not os.path.isdir(folder) or name not in os.listdir(folder):
        raise not_found("File")
    path = os.path.join(folder, name)
    if not os.path.isfile(path):
        raise not_found("File")
    return path


def read_upload(upload) -> bytes:
    data = upload.file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise UserError(f"{upload.filename or 'File'} is larger than 20 MB")
    return data


# ---------- data folders ----------

def job_dir(data_dir: str, job_id: int) -> str:
    return os.path.join(data_dir, "jobs", str(job_id))


def relief_dir(data_dir: str, relief_id: str) -> str:
    return os.path.join(data_dir, "relief", relief_id)


# ---------- rows -> dicts ----------

def machine_out(m: Machine | None) -> dict | None:
    if m is None:
        return None
    return dict(id=m.id, name=m.name, kind=m.kind, bed_w=m.bed_w, bed_h=m.bed_h,
                rate_per_hour=m.rate_per_hour, notes=m.notes or "")


def tool_out(t: Tool) -> dict:
    return dict(id=t.id, name=t.name, diameter_mm=t.diameter_mm, kind=t.kind, angle_deg=t.angle_deg,
                feed_mm_min=t.feed_mm_min, plunge_mm_min=t.plunge_mm_min, rpm=t.rpm,
                pass_depth_mm=t.pass_depth_mm)


def material_out(m: Material | None) -> dict | None:
    if m is None:
        return None
    return dict(id=m.id, name=m.name, thickness_mm=m.thickness_mm, sheet_w=m.sheet_w, sheet_h=m.sheet_h,
                price_per_sheet=m.price_per_sheet, margin_mm=m.margin_mm)


def offcut_out(o: Offcut) -> dict:
    return dict(id=o.id, material_id=o.material_id, width=round(o.width, 2), height=round(o.height, 2),
                outline_wkt=o.outline_wkt, source_job_id=o.source_job_id, created_at=iso(o.created_at),
                used=bool(o.used))


def cam_tool(t: Tool):
    from cam.toolpaths import CamTool
    return CamTool(id=str(t.id), name=t.name, diameter_mm=t.diameter_mm, kind=t.kind, angle_deg=t.angle_deg,
                   feed_mm_min=t.feed_mm_min, plunge_mm_min=t.plunge_mm_min, rpm=t.rpm,
                   pass_depth_mm=t.pass_depth_mm)


# ---------- parts ----------

def part_from_row(r: JobPart) -> Part:
    shape = shapely_wkt.loads(r.shape_wkt)
    marks = [shapely_wkt.loads(m) for m in json.loads(r.marks_wkt or "[]")]
    return Part(id=r.part_id, name=r.name, shape=shape, qty=r.qty, marks=marks,
                mark_layer=r.mark_layer, source=r.source)


def part_rows(s: Session, job_id: int) -> list[JobPart]:
    return list(s.scalars(select(JobPart).where(JobPart.job_id == job_id)
                          .order_by(JobPart.position, JobPart.pk)))


def job_parts(s: Session, job_id: int) -> list[Part]:
    return [part_from_row(r) for r in part_rows(s, job_id)]


def part_out(p: Part) -> dict:
    from core.svgout import part_svg
    return dict(id=p.id, name=p.name, qty=p.qty, width=round(p.width, 2), height=round(p.height, 2),
                area_mm2=round(p.area, 1), holes=len(p.shape.interiors), marks=len(p.marks),
                mark_layer=p.mark_layer, source=p.source, svg=part_svg(p))


def import_files(s: Session, job: Job, uploads: list) -> dict:
    """Read each uploaded file, append its parts to the job."""
    from core import importers

    if not uploads:
        raise UserError("Choose a file to upload")
    reports = []
    for up in uploads:
        data = read_upload(up)
        name = os.path.basename(up.filename or "upload")
        try:
            rep = importers.import_file(data, name)
        except importers.ImportError_ as e:
            raise UserError(f"{name}: {e}") from e
        except ValueError as e:
            raise UserError(f"{name}: {e}") from e
        reports.append((name, rep))

    existing = {r.part_id for r in part_rows(s, job.id)}
    pos = len(existing)
    new_parts, warnings = [], []
    for name, rep in reports:
        warnings += [f"{name}: {w}" for w in rep.warnings]
        for p in rep.parts:
            pid = p.id
            while pid in existing:
                pid = uuid.uuid4().hex[:8]
            p.id = pid
            existing.add(pid)
            if not p.source:
                p.source = name
            s.add(JobPart(job_id=job.id, part_id=p.id, position=pos, name=p.name, qty=p.qty,
                          shape_wkt=p.shape.wkt, marks_wkt=json.dumps([m.wkt for m in p.marks]),
                          mark_layer=p.mark_layer or "engrave", source=p.source))
            pos += 1
            new_parts.append(p)
    s.commit()
    return {"parts": [part_out(p) for p in new_parts], "warnings": warnings}


# ---------- defaults ----------

def default_cut_tool(s: Session) -> Tool | None:
    """Same rule as the web UI: the 6 mm flat bit, else the smallest flat bit,
    else any bit."""
    tools = list(s.scalars(select(Tool).order_by(Tool.diameter_mm, Tool.id)))
    flat = [t for t in tools if t.kind == "flat"]
    six = [t for t in flat if abs(t.diameter_mm - 6.0) < 1e-9]
    return (six or flat or tools or [None])[0]


def default_gap(s: Session, machine: Machine | None) -> float:
    if machine is not None and machine.kind == "laser":
        return 2.0
    t = default_cut_tool(s)
    return round((t.diameter_mm if t else 6.0) + 1.0, 3)


def default_settings(s: Session, job: Job) -> dict:
    mat = s.get(Material, job.material_id) if job.material_id else None
    mach = s.get(Machine, job.machine_id) if job.machine_id else None
    return dict(gap_mm=default_gap(s, mach), rotations=list(DEFAULT_ROTATIONS), allow_in_holes=True,
                resolution_mm=2.0, time_limit_s=10.0, seed=1, use_offcuts=False,
                margin_mm=mat.margin_mm if mat else 10.0,
                sheet_w=mat.sheet_w if mat else None, sheet_h=mat.sheet_h if mat else None)


# ---------- nest result <-> JSON ----------

def result_to_json(r: NestResult) -> dict:
    return {
        "sheets": [dict(sheet_id=sh.sheet_id, index=sh.index, width=sh.width, height=sh.height,
                        placements=[asdict(p) for p in sh.placements], used_area=sh.used_area,
                        utilisation=sh.utilisation, offcut=sh.offcut.wkt if sh.offcut is not None else None,
                        is_offcut=sh.is_offcut) for sh in r.sheets],
        "unplaced": dict(r.unplaced), "utilisation": r.utilisation, "elapsed_s": r.elapsed_s,
        "attempts": r.attempts,
    }


def result_from_json(d: dict) -> NestResult:
    sheets = [SheetResult(sheet_id=x["sheet_id"], index=x["index"], width=x["width"], height=x["height"],
                          placements=[Placement(**p) for p in x["placements"]], used_area=x["used_area"],
                          utilisation=x["utilisation"],
                          offcut=shapely_wkt.loads(x["offcut"]) if x.get("offcut") else None,
                          is_offcut=x.get("is_offcut", False)) for x in d["sheets"]]
    return NestResult(sheets=sheets, unplaced=dict(d.get("unplaced", {})), utilisation=d["utilisation"],
                      elapsed_s=d.get("elapsed_s", 0.0), attempts=d.get("attempts", 1))


def latest_nest(s: Session, job_id: int) -> NestRun | None:
    return s.scalars(select(NestRun).where(NestRun.job_id == job_id).order_by(NestRun.id.desc())).first()


# ---------- nesting ----------

def _cut_estimate(parts: dict[str, Part], result: NestResult, tool: Tool | None,
                  thickness: float | None, laser: bool) -> float:
    """Rough cut time in minutes: perimeters x passes / feed."""
    length = 0.0
    for sh in result.sheets:
        for pl in sh.placements:
            p = parts.get(pl.part_id)
            if p is None:
                continue
            length += p.shape.exterior.length + sum(h.length for h in p.shape.interiors)
    feed = tool.feed_mm_min if tool else 3000.0
    if laser or tool is None or not thickness:
        passes = 1
    else:
        passes = max(1, math.ceil((thickness + DEFAULT_CUT_THROUGH) / tool.pass_depth_mm - 1e-9))
    return length * passes / feed if feed > 0 else 0.0


def build_nest_out(s: Session, job: Job, parts: list[Part], result: NestResult) -> dict:
    from core.svgout import sheet_svg

    pmap = {p.id: p for p in parts}
    mat = s.get(Material, job.material_id) if job.material_id else None
    mach = s.get(Machine, job.machine_id) if job.machine_id else None
    sheets = []
    for sh in result.sheets:
        oc = None
        if sh.offcut is not None and not sh.offcut.is_empty:
            b = sh.offcut.bounds
            oc = {"width": round(b[2] - b[0], 1), "height": round(b[3] - b[1], 1)}
        sheets.append(dict(index=sh.index, sheet_id=sh.sheet_id, width=sh.width, height=sh.height,
                           is_offcut=bool(sh.is_offcut), utilisation=round(sh.utilisation, 4),
                           parts_count=len(sh.placements), svg=sheet_svg(sh, pmap), offcut=oc))
    full_sheets = sum(1 for sh in result.sheets if not sh.is_offcut)
    price = mat.price_per_sheet if mat else 0.0
    minutes = _cut_estimate(pmap, result, default_cut_tool(s), mat.thickness_mm if mat else None,
                            laser=bool(mach and mach.kind == "laser"))
    rate = mach.rate_per_hour if mach else 0.0
    sheets_thb = round(full_sheets * price, 2)
    machine_thb = round(minutes / 60.0 * rate, 2)
    return dict(
        sheets=sheets,
        unplaced=[dict(part_id=pid, name=pmap[pid].name if pid in pmap else pid, count=n)
                  for pid, n in result.unplaced.items() if n > 0],
        utilisation=round(result.utilisation, 4), sheets_used=len(result.sheets),
        attempts=result.attempts, elapsed_s=round(result.elapsed_s, 2),
        cost=dict(sheets_thb=sheets_thb, machine_thb=machine_thb, total_thb=round(sheets_thb + machine_thb, 2),
                  cut_minutes=round(minutes, 1), vat_included=False),
    )


def run_nest(s: Session, data_dir: str, job: Job, body) -> dict:
    from nesting import engine

    opts = default_settings(s, job)
    given = body.model_dump(exclude_none=True)
    opts.update(given)
    opts["time_limit_s"] = max(0.0, min(float(opts["time_limit_s"]), MAX_TIME_LIMIT))
    if not all(math.isfinite(float(r)) for r in (opts["rotations"] or [])):
        raise UserError("Rotations must be numbers of degrees")   # JSON NaN/Infinity would crash the nester
    rots = [float(r) % 360.0 for r in (opts["rotations"] or [0.0])]
    opts["rotations"] = list(dict.fromkeys(rots))       # drop repeats, keep order

    if not opts.get("sheet_w") or not opts.get("sheet_h"):
        raise UserError("Choose a material or enter the sheet size")
    parts = job_parts(s, job.id)
    if not any(p.qty > 0 for p in parts):
        raise UserError("Add parts (quantity above 0) before nesting")

    # A sheet bigger than the machine bed cannot be cut: nest on the part of
    # it that fits the bed (either way round) and keep the rest whole.
    mach = s.get(Machine, job.machine_id) if job.machine_id else None
    if mach is not None and mach.bed_w and mach.bed_h:
        sw, sh = float(opts["sheet_w"]), float(opts["sheet_h"])
        big, small = max(mach.bed_w, mach.bed_h), min(mach.bed_w, mach.bed_h)
        if not ((sw <= mach.bed_w and sh <= mach.bed_h) or (sw <= mach.bed_h and sh <= mach.bed_w)):
            if sw >= sh:
                opts["sheet_w"], opts["sheet_h"] = min(sw, big), min(sh, small)
            else:
                opts["sheet_w"], opts["sheet_h"] = min(sw, small), min(sh, big)

    margin = float(opts["margin_mm"])
    sheets: list[SheetSpec] = []
    used_offcuts: list[int] = []
    if opts["use_offcuts"] and job.material_id:
        rows = list(s.scalars(select(Offcut).where(Offcut.material_id == job.material_id,
                                                   Offcut.used.is_(False))))
        rows.sort(key=lambda o: (o.width * o.height, o.id))   # smallest first, so big sheets stay whole
        for o in rows:
            outline = shapely_wkt.loads(o.outline_wkt)
            b = outline.bounds
            outline = translate(outline, -b[0], -b[1])
            sheets.append(SheetSpec(id=f"offcut-{o.id}", width=o.width, height=o.height, margin=margin,
                                    qty=1, outline=outline, is_offcut=True, name=f"Offcut {o.id}"))
            used_offcuts.append(o.id)
    sheets.append(SheetSpec(id="sheet", width=float(opts["sheet_w"]), height=float(opts["sheet_h"]),
                            margin=margin, qty=None, name="Sheet"))

    ns = NestSettings(gap_mm=float(opts["gap_mm"]), rotations=opts["rotations"],
                      allow_in_holes=bool(opts["allow_in_holes"]), resolution_mm=float(opts["resolution_mm"]),
                      time_limit_s=opts["time_limit_s"], seed=int(opts["seed"]))
    t0 = time.perf_counter()
    try:
        result = engine.nest([p for p in parts if p.qty > 0], sheets, ns)
    except ValueError as e:
        raise UserError(str(e)) from e
    if not result.elapsed_s:
        result.elapsed_s = time.perf_counter() - t0

    out = build_nest_out(s, job, parts, result)
    for old in s.scalars(select(NestRun).where(NestRun.job_id == job.id)):
        s.delete(old)
    s.add(NestRun(job_id=job.id, settings_json=json.dumps(opts),
                  result_json=json.dumps({"result": result_to_json(result), "out": out})))
    clear_exports(data_dir, job)                         # re-nesting makes old files wrong
    job.status = "nested"
    s.commit()
    return out


# ---------- exports ----------

def exports_of(job: Job) -> list[dict]:
    return json.loads(job.exports_json or "[]")


def file_out(job_id: int, rec: dict) -> dict:
    return dict(name=rec["name"], url=f"/api/jobs/{job_id}/files/{rec['name']}", size=rec.get("size", 0),
                minutes=rec.get("minutes"), tool=rec.get("tool"), target=rec.get("target"),
                sheet=rec.get("sheet"))


def clear_exports(data_dir: str, job: Job, target: str | None = None) -> None:
    keep = []
    folder = job_dir(data_dir, job.id)
    for rec in exports_of(job):
        if target is None or rec.get("target") == target:
            path = os.path.join(folder, rec["name"])
            if is_safe_name(rec["name"]) and os.path.isfile(path):
                os.remove(path)
        else:
            keep.append(rec)
    job.exports_json = json.dumps(keep)


def run_export(s: Session, data_dir: str, job: Job, body) -> dict:
    run = latest_nest(s, job.id)
    if run is None:
        raise UserError("Nest the job before making files", 409)
    result = result_from_json(json.loads(run.result_json)["result"])
    parts = {p.id: p for p in job_parts(s, job.id)}
    warnings: list[str] = []
    missing = {pl.part_id for sh in result.sheets for pl in sh.placements if pl.part_id not in parts}
    if missing:
        warnings.append(f"{len(missing)} part(s) were deleted after nesting and are left out: nest again")
        for sh in result.sheets:
            sh.placements = [pl for pl in sh.placements if pl.part_id in parts]
    # parts added or quantities changed after nesting are not in these files
    nested: dict[str, int] = {}
    for sh in result.sheets:
        for pl in sh.placements:
            nested[pl.part_id] = nested.get(pl.part_id, 0) + 1
    changed = [p.name for p in parts.values()
               if nested.get(p.id, 0) + int(result.unplaced.get(p.id, 0)) != p.qty]
    if changed:
        warnings.append(f"Parts or quantities changed after nesting ({', '.join(changed[:5])}"
                        f"{', ...' if len(changed) > 5 else ''}): these files use the old layout, nest again")

    folder = job_dir(data_dir, job.id)
    os.makedirs(folder, exist_ok=True)
    base = slug(job.name, f"job{job.id}")
    records: list[dict] = []

    gap = float(json.loads(run.settings_json).get("gap_mm", 0) or 0)
    if body.target == "ncstudio":
        records, w = _export_ncstudio(s, job, result, parts, body, folder, base, gap)
    else:
        # the beam runs kerf/2 outside each part and burns kerf/2 either side of that line
        if (body.kerf_mm or 0.0) > gap + 1e-6:
            raise UserError(f"The parts were nested {gap:g} mm apart, less than the {body.kerf_mm:g} mm kerf: "
                            f"nest again with a gap of at least {body.kerf_mm:g} mm, or lower the kerf")
        records, w = _export_ruida(result, parts, body, folder, base)
    warnings += w

    # Only now drop the earlier files of this target: a refused export (bad gap, no
    # material...) must not delete files the job still lists. Same names were overwritten.
    new_names = {r["name"] for r in records}
    for rec in exports_of(job):
        if rec.get("target") == body.target and rec["name"] not in new_names and is_safe_name(rec["name"]):
            old = os.path.join(folder, rec["name"])
            if os.path.isfile(old):
                os.remove(old)
    job.exports_json = json.dumps([r for r in exports_of(job) if r.get("target") != body.target] + records)
    if job.status in ("draft", "nested", "exported"):
        job.status = "exported"
    s.commit()
    return {"files": [file_out(job.id, r) for r in records], "warnings": warnings}


def _export_ncstudio(s: Session, job: Job, result: NestResult, parts: dict[str, Part], body,
                     folder: str, base: str, gap_mm: float | None = None) -> tuple[list[dict], list[str]]:
    from cam.ncstudio import write_ncstudio
    from cam.toolpaths import CamSettings, sheet_toolpaths

    mat = s.get(Material, job.material_id) if job.material_id else None
    if mat is None:
        raise UserError("Choose a material first: the G-code needs its thickness")
    if body.cut_tool_id is not None:
        cut_row = s.get(Tool, body.cut_tool_id)
        if cut_row is None:
            raise UserError("Cut tool not found")
    else:
        cut_row = default_cut_tool(s)
        if cut_row is None:
            raise UserError("Add a tool in Settings first")
    # Profiles cut right through the sheet; a V-bit only cuts with its short cone and
    # its offset (diameter / 2) is wrong at every depth but one.
    if cut_row.kind == "vbit":
        raise UserError(f"The {cut_row.name} cannot cut parts out: pick a flat bit as the cut tool "
                        f"(a V-bit can be the mark tool)")
    # Parts closer together than the bit is wide would be cut into by the
    # neighbour's outside path, so refuse instead of writing unsafe G-code.
    if gap_mm is not None and gap_mm + 1e-6 < cut_row.diameter_mm + 0.5:
        raise UserError(f"The parts were nested {gap_mm:g} mm apart but the {cut_row.name} is "
                        f"{cut_row.diameter_mm:g} mm wide: nest again with a gap of at least "
                        f"{cut_row.diameter_mm + 1:g} mm, or pick a thinner bit")
    mark_row = None
    if body.mark_tool_id is not None:
        mark_row = s.get(Tool, body.mark_tool_id)
        if mark_row is None:
            raise UserError("Mark tool not found")

    cam_opts = body.cam.model_dump(exclude_none=True) if body.cam else {}
    try:
        cs = CamSettings(thickness_mm=mat.thickness_mm, **cam_opts)
    except TypeError as e:
        raise UserError(f"Unknown CAM setting: {e}") from e
    cut_tool = cam_tool(cut_row)
    mark_tool = cam_tool(mark_row) if mark_row is not None else None
    tools = {cut_tool.id: cut_tool}
    if mark_tool is not None:
        tools[mark_tool.id] = mark_tool

    records, warnings = [], []
    for sh in result.sheets:
        n = sh.index + 1
        try:
            paths, w = sheet_toolpaths(sh, parts, cut_tool, mark_tool, cs)
        except ValueError as e:
            raise UserError(str(e)) from e
        warnings += [f"Sheet {n}: {x}" for x in w]
        # One file per bit, in the order the paths use them (marks first).
        order: list[str] = []
        for p in paths:
            if p.tool_id not in order:
                order.append(p.tool_id)
        for tid in order:
            tool = tools.get(tid, cut_tool)
            name = f"{base}_sheet{n:02d}_{slug(tool.name, 'tool' + tid)}.nc"
            path = os.path.join(folder, name)
            info = write_ncstudio(path, [p for p in paths if p.tool_id == tid], tool,
                                  title=f"{job.name} sheet {n}", sheet_w=sh.width, sheet_h=sh.height,
                                  safe_z=cs.safe_z)
            records.append(dict(name=name, size=os.path.getsize(path), minutes=round(info.minutes, 1),
                                tool=tool.name, target="ncstudio", sheet=n))
        if not paths:
            warnings.append(f"Sheet {n}: nothing to cut")
    return records, warnings


def _export_ruida(result: NestResult, parts: dict[str, Part], body, folder: str,
                  base: str) -> tuple[list[dict], list[str]]:
    from cam.ruida import write_ruida_dxf

    records, warnings = [], []
    for sh in result.sheets:
        n = sh.index + 1
        name = f"{base}_sheet{n:02d}.dxf"
        path = os.path.join(folder, name)
        try:
            count = write_ruida_dxf(path, sh, parts, kerf_mm=body.kerf_mm or 0.0,
                                    include_sheet_outline=bool(body.include_sheet_outline))
        except ValueError as e:
            raise UserError(str(e)) from e
        if not count:
            warnings.append(f"Sheet {n}: nothing to cut")
        records.append(dict(name=name, size=os.path.getsize(path), minutes=None, tool=None,
                            target="ruida", sheet=n))
    return records, warnings


def export_zip(data_dir: str, job: Job) -> bytes:
    recs = exports_of(job)
    folder = job_dir(data_dir, job.id)
    names = [r["name"] for r in recs if is_safe_name(r["name"]) and os.path.isfile(os.path.join(folder, r["name"]))]
    if not names:
        raise not_found("Exported files")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for n in names:
            z.write(os.path.join(folder, n), n)
    return buf.getvalue()


# ---------- commit (mark as cut) ----------

def commit_job(s: Session, job: Job) -> tuple[list[Offcut], list[int]]:
    run = latest_nest(s, job.id)
    if run is None:
        raise UserError("Nest the job before marking it as cut", 409)
    if job.status == "cut":
        raise UserError("This job is already marked as cut", 409)
    result = result_from_json(json.loads(run.result_json)["result"])
    saved: list[Offcut] = []
    used: list[int] = []
    for sh in result.sheets:
        if sh.is_offcut and sh.sheet_id.startswith("offcut-") and sh.placements:
            try:
                oid = int(sh.sheet_id.split("-", 1)[1])
            except ValueError:
                continue
            o = s.get(Offcut, oid)
            if o is not None and not o.used:
                o.used = True
                used.append(oid)
        if sh.offcut is not None and not sh.offcut.is_empty and job.material_id:
            poly = sh.offcut
            b = poly.bounds
            poly = translate(poly, -b[0], -b[1])
            o = Offcut(material_id=job.material_id, width=round(b[2] - b[0], 2), height=round(b[3] - b[1], 2),
                       outline_wkt=poly.wkt, source_job_id=job.id, used=False)
            s.add(o)
            saved.append(o)
    job.status = "cut"
    s.commit()
    return saved, used


# ---------- job detail ----------

def job_list_item(s: Session, job: Job, mats: dict, machs: dict) -> dict:
    run = latest_nest(s, job.id)
    sheets, util = 0, None
    if run is not None:
        out = json.loads(run.result_json)["out"]
        sheets, util = out["sheets_used"], out["utilisation"]
    m, mc = mats.get(job.material_id), machs.get(job.machine_id)
    return dict(id=job.id, name=job.name, material=m.name if m else None, machine=mc.name if mc else None,
                material_id=job.material_id, machine_id=job.machine_id, status=job.status, sheets=sheets,
                utilisation=util, created_at=iso(job.created_at))


def job_detail(s: Session, job: Job) -> dict:
    mat = s.get(Material, job.material_id) if job.material_id else None
    mach = s.get(Machine, job.machine_id) if job.machine_id else None
    run = latest_nest(s, job.id)
    settings = default_settings(s, job)
    nest = None
    if run is not None:
        settings.update(json.loads(run.settings_json))
        nest = json.loads(run.result_json)["out"]
    return dict(id=job.id, name=job.name, material=material_out(mat), machine=machine_out(mach),
                material_id=job.material_id, machine_id=job.machine_id, status=job.status,
                created_at=iso(job.created_at), parts=[part_out(p) for p in job_parts(s, job.id)],
                settings={k: settings[k] for k in ("gap_mm", "rotations", "allow_in_holes", "resolution_mm",
                                                    "time_limit_s", "seed", "use_offcuts", "margin_mm",
                                                    "sheet_w", "sheet_h")},
                nest=nest, exports=[file_out(job.id, r) for r in exports_of(job)])


def delete_job(s: Session, data_dir: str, job: Job) -> None:
    for model in (JobPart, NestRun):
        for row in s.scalars(select(model).where(model.job_id == job.id)):
            s.delete(row)
    s.delete(job)
    s.commit()
    shutil.rmtree(job_dir(data_dir, job.id), ignore_errors=True)


# ---------- relief ----------

def relief_param_names() -> list[str]:
    from relief.api import ReliefParams
    return [f.name for f in fields(ReliefParams)]


def _relief_out(run: ReliefRun) -> dict:
    res = json.loads(run.result_json or "{}")
    base = f"/api/relief/{run.id}/files/"
    return dict(id=run.id, **res, image_urls=[base + n for n in res.get("images", [])],
                file_urls=[base + f["name"] for f in res.get("files", [])],
                params=json.loads(run.params_json))


def _run_relief(run: ReliefRun, params: dict, data_dir: str) -> dict:
    from relief.api import ReliefError, ReliefParams, run_relief

    folder = relief_dir(data_dir, run.id)
    src = os.path.basename(run.image_path)
    for n in os.listdir(folder):                 # old outputs go; the picture stays
        if n != src and os.path.isfile(os.path.join(folder, n)):
            os.remove(os.path.join(folder, n))
    try:
        p = ReliefParams(**params)
        return run_relief(run.image_path, folder, p, base=params_base(run))
    except ReliefError as e:
        raise UserError(str(e)) from e
    except (TypeError, ValueError) as e:
        raise UserError(f"Relief settings: {e}") from e
    except OSError as e:                       # Pillow cannot read the picture
        raise UserError("The picture could not be read: use a PNG or JPG file") from e


def params_base(run: ReliefRun) -> str:
    """Output file stem: the uploaded picture's name, made safe."""
    stem = os.path.splitext(os.path.basename(run.image_path))[0]
    stem = stem[len("source_"):] if stem.startswith("source_") else stem
    return slug(stem, "relief")


def create_relief(s: Session, data_dir: str, upload, params: dict) -> dict:
    data = read_upload(upload)
    if not data:
        raise UserError("Choose a picture")
    rid = uuid.uuid4().hex[:12]
    folder = relief_dir(data_dir, rid)
    os.makedirs(folder, exist_ok=True)
    orig = os.path.basename(upload.filename or "image.png")
    stem, ext = os.path.splitext(orig)
    ext = ext.lower() if re.fullmatch(r"\.[A-Za-z0-9]{1,5}", ext or "") else ".png"
    img = os.path.join(folder, f"source_{slug(stem, 'image')}{ext}")
    with open(img, "wb") as fh:
        fh.write(data)
    run = ReliefRun(id=rid, image_path=img, params_json=json.dumps(params))
    try:
        res = _run_relief(run, params, data_dir)
    except UserError:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    run.result_json = json.dumps(res)
    s.add(run)
    s.commit()
    return _relief_out(run)


def rerun_relief(s: Session, data_dir: str, run: ReliefRun, changes: dict) -> dict:
    params = json.loads(run.params_json)
    params.update(changes)
    res = _run_relief(run, params, data_dir)
    run.params_json = json.dumps(params)
    run.result_json = json.dumps(res)
    s.commit()
    return _relief_out(run)


def relief_out(run: ReliefRun) -> dict:
    return _relief_out(run)

