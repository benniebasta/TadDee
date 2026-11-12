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
from datetime import datetime

from shapely import wkt as shapely_wkt
from shapely.affinity import translate
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.models import NestResult, NestSettings, Part, Placement, SheetResult, SheetSpec

from .models import FeedOverride, Job, JobPart, Machine, Material, NestRun, Offcut, ReliefRun, Tool

VERSION = "0.1.0"
MAX_UPLOAD = 20 * 1024 * 1024          # 20 MB per file
MAX_TIME_LIMIT = 60.0
DEFAULT_ROTATIONS = [0.0, 90.0, 180.0, 270.0]
DEFAULT_CUT_THROUGH = 0.3


class UserError(Exception):
    """Something the user can fix. Becomes an HTTP error with this sentence."""

    def __init__(self, message: str, status: int = 400, extra: dict | None = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.extra = extra or {}     # more keys for the JSON body (e.g. "issues")


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
                pass_depth_mm=t.pass_depth_mm, ramp_mm=_ramp(t))


def _ramp(t: Tool) -> float:
    return 12.0 if t.ramp_mm is None else float(t.ramp_mm)


def feed_out(f: FeedOverride) -> dict:
    return dict(id=f.id, material_id=f.material_id, tool_id=f.tool_id, feed_mm_min=f.feed_mm_min,
                plunge_mm_min=f.plunge_mm_min, rpm=f.rpm, pass_depth_mm=f.pass_depth_mm, notes=f.notes or "")


def feed_for(s: Session, material_id: int | None, tool_id: int | None) -> FeedOverride | None:
    """The feeds-and-speeds row for this material and bit, if the shop made one."""
    if material_id is None or tool_id is None:
        return None
    return s.scalars(select(FeedOverride).where(FeedOverride.material_id == material_id,
                                                FeedOverride.tool_id == tool_id)).first()


def material_out(m: Material | None) -> dict | None:
    if m is None:
        return None
    return dict(id=m.id, name=m.name, thickness_mm=m.thickness_mm, sheet_w=m.sheet_w, sheet_h=m.sheet_h,
                price_per_sheet=m.price_per_sheet, margin_mm=m.margin_mm)


def offcut_out(o: Offcut) -> dict:
    return dict(id=o.id, material_id=o.material_id, width=round(o.width, 2), height=round(o.height, 2),
                outline_wkt=o.outline_wkt, source_job_id=o.source_job_id, created_at=iso(o.created_at),
                used=bool(o.used))


def cam_tool(t: Tool, override: FeedOverride | None = None):
    """CamTool from the bit row; values in the material's feeds-and-speeds
    row (when there is one) win over the bit defaults, field by field."""
    from cam.toolpaths import CamTool

    def pick(name):
        v = getattr(override, name, None) if override is not None else None
        return getattr(t, name) if v is None else v
    return CamTool(id=str(t.id), name=t.name, diameter_mm=t.diameter_mm, kind=t.kind, angle_deg=t.angle_deg,
                   feed_mm_min=pick("feed_mm_min"), plunge_mm_min=pick("plunge_mm_min"), rpm=int(pick("rpm")),
                   pass_depth_mm=max(0.1, float(pick("pass_depth_mm"))), ramp_mm=_ramp(t))


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
    cut = default_cut_tool(s)
    if cut is not None and mat is not None:
        cut = cam_tool(cut, feed_for(s, mat.id, cut.id))      # same fields as a Tool row
    minutes = _cut_estimate(pmap, result, cut, mat.thickness_mm if mat else None,
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
#
# Every successful export is a new revision of the job (1, 2, 3...). Its files
# go in <job folder>/rev<NNN>/ and carry the revision in their name, so nothing
# is ever overwritten: the operator can always prove which file was cut. The
# job lists every file; `current` marks the latest revision of each target
# made since the latest nest (a re-nest keeps the old files, as not current).

def exports_of(job: Job) -> list[dict]:
    return json.loads(job.exports_json or "[]")


def _current_names(recs: list[dict]) -> set[str]:
    latest: dict[str, int] = {}
    for r in recs:
        if not r.get("stale"):
            t = r.get("target") or ""
            latest[t] = max(latest.get(t, 0), int(r.get("revision") or 0))
    return {r["name"] for r in recs
            if not r.get("stale") and int(r.get("revision") or 0) == latest.get(r.get("target") or "", -1)}


def file_out(job_id: int, rec: dict, current: bool = True) -> dict:
    return dict(name=rec["name"], url=f"/api/jobs/{job_id}/files/{rec['name']}", size=rec.get("size", 0),
                minutes=rec.get("minutes"), tool=rec.get("tool"), target=rec.get("target"),
                sheet=rec.get("sheet"), revision=rec.get("revision"), current=current,
                created_at=rec.get("created_at"))


def files_out(job: Job) -> list[dict]:
    """All export files of the job, newest revision first."""
    recs = exports_of(job)
    cur = _current_names(recs)
    order = sorted(range(len(recs)), key=lambda i: (-int(recs[i].get("revision") or 0), i))
    return [file_out(job.id, recs[i], recs[i]["name"] in cur) for i in order]


def clear_exports(data_dir: str, job: Job, target: str | None = None) -> None:
    """After a re-nest the files no longer match the layout: keep them (proof of
    what was cut), but mark them as not current."""
    recs = exports_of(job)
    for rec in recs:
        if target is None or rec.get("target") == target:
            rec["stale"] = True
    job.exports_json = json.dumps(recs)


def rev_dirname(rev: int) -> str:
    return f"rev{rev:03d}"


def next_revision(data_dir: str, job: Job) -> int:
    """One more than every revision the job has listed or left on disk."""
    top = max([int(r.get("revision") or 0) for r in exports_of(job)] + [0])
    folder = job_dir(data_dir, job.id)
    if os.path.isdir(folder):
        for n in os.listdir(folder):
            m = re.fullmatch(r"rev(\d+)", n)
            if m:
                top = max(top, int(m.group(1)))
    return top + 1


def job_file_path(data_dir: str, job: Job, name: str) -> str:
    """Path of one listed export file (in its revision folder); older files
    that sit directly in the job folder still work."""
    if not is_safe_name(name):
        raise UserError("Bad file name", 400)
    folder = job_dir(data_dir, job.id)
    for rec in exports_of(job):
        if rec["name"] == name and rec.get("dir"):
            d = rec["dir"]
            if not is_safe_name(d):
                raise not_found("File")
            return safe_file(os.path.join(folder, d), name)
    return safe_file(folder, name)


class _Ctx:
    """Everything an export or a check needs, resolved once."""
    result: NestResult
    parts: dict
    gap: float
    target: str
    mat: Material | None = None
    mach: Machine | None = None
    cut_row: Tool | None = None
    cut_tool = None
    mark_tool = None
    cam = None
    kerf: float = 0.0
    warnings: list


def _context(s: Session, job: Job, target: str, *, cut_tool_id=None, mark_tool_id=None,
             cam_opts: dict | None = None, kerf_mm: float | None = None) -> _Ctx:
    run = latest_nest(s, job.id)
    if run is None:
        raise UserError("Nest the job before making files", 409)
    c = _Ctx()
    c.target = target
    c.result = result_from_json(json.loads(run.result_json)["result"])
    c.parts = {p.id: p for p in job_parts(s, job.id)}
    c.warnings = []
    c.mat = s.get(Material, job.material_id) if job.material_id else None
    c.mach = s.get(Machine, job.machine_id) if job.machine_id else None
    c.kerf = float(kerf_mm or 0.0)
    missing = {pl.part_id for sh in c.result.sheets for pl in sh.placements if pl.part_id not in c.parts}
    if missing:
        c.warnings.append(f"{len(missing)} part(s) were deleted after nesting and are left out: nest again")
        for sh in c.result.sheets:
            sh.placements = [pl for pl in sh.placements if pl.part_id in c.parts]
    # parts added or quantities changed after nesting are not in these files
    nested: dict[str, int] = {}
    for sh in c.result.sheets:
        for pl in sh.placements:
            nested[pl.part_id] = nested.get(pl.part_id, 0) + 1
    changed = [p.name for p in c.parts.values()
               if nested.get(p.id, 0) + int(c.result.unplaced.get(p.id, 0)) != p.qty]
    if changed:
        c.warnings.append(f"Parts or quantities changed after nesting ({', '.join(changed[:5])}"
                          f"{', ...' if len(changed) > 5 else ''}): these files use the old layout, nest again")
    c.gap = float(json.loads(run.settings_json).get("gap_mm", 0) or 0)
    if target != "ncstudio":
        return c

    from cam.toolpaths import CamSettings

    if c.mat is None:
        raise UserError("Choose a material first: the G-code needs its thickness")
    if cut_tool_id is not None:
        c.cut_row = s.get(Tool, cut_tool_id)
        if c.cut_row is None:
            raise UserError("Cut tool not found")
    else:
        c.cut_row = default_cut_tool(s)
        if c.cut_row is None:
            raise UserError("Add a tool in Settings first")
    mark_row = None
    if mark_tool_id is not None:
        mark_row = s.get(Tool, mark_tool_id)
        if mark_row is None:
            raise UserError("Mark tool not found")
    try:
        c.cam = CamSettings(thickness_mm=c.mat.thickness_mm, **(cam_opts or {}))
    except TypeError as e:
        raise UserError(f"Unknown CAM setting: {e}") from e
    c.cut_tool = cam_tool(c.cut_row, feed_for(s, c.mat.id, c.cut_row.id))
    c.mark_tool = cam_tool(mark_row, feed_for(s, c.mat.id, mark_row.id)) if mark_row is not None else None
    return c


def _issues(c: _Ctx) -> list[dict]:
    from cam.checks import pre_export_checks

    bed_w = c.mach.bed_w if c.mach else 0.0
    bed_h = c.mach.bed_h if c.mach else 0.0
    try:
        return pre_export_checks(c.result.sheets, c.parts, target=c.target, cut_tool=c.cut_tool,
                                 mark_tool=c.mark_tool, cam=c.cam, gap_mm=c.gap, bed_w=bed_w or 0.0,
                                 bed_h=bed_h or 0.0, kerf_mm=c.kerf)
    except ValueError as e:
        raise UserError(str(e)) from e


def run_checks(s: Session, job: Job, q) -> dict:
    """GET /jobs/{id}/checks: the issues an export with these choices would meet."""
    target = q.target or ("ruida" if job.machine_id and (m := s.get(Machine, job.machine_id))
                          and m.kind == "laser" else "ncstudio")
    cam_keys = ("cut_through_mm", "safe_z", "climb", "tabs", "tab_width_mm", "tab_height_mm",
                "tab_min_part_mm", "engrave_depth_mm", "holes_first", "lead_in", "ramp_max_angle_deg")
    given = q.model_dump(exclude_none=True)
    c = _context(s, job, target, cut_tool_id=q.cut_tool_id, mark_tool_id=q.mark_tool_id,
                 cam_opts={k: v for k, v in given.items() if k in cam_keys}, kerf_mm=q.kerf_mm)
    issues = _issues(c)
    return dict(target=target, issues=issues, blocks=sum(1 for i in issues if i["severity"] == "block"),
                warns=sum(1 for i in issues if i["severity"] == "warn"))


def run_export(s: Session, data_dir: str, job: Job, body) -> dict:
    from cam.checks import summary

    cam_opts = body.cam.model_dump(exclude_none=True) if body.cam else {}
    c = _context(s, job, body.target, cut_tool_id=body.cut_tool_id, mark_tool_id=body.mark_tool_id,
                 cam_opts=cam_opts, kerf_mm=body.kerf_mm)
    if body.target == "ncstudio":
        # Profiles cut right through the sheet; a V-bit only cuts with its short cone and
        # its offset (diameter / 2) is wrong at every depth but one.
        if c.cut_row.kind == "vbit":
            raise UserError(f"The {c.cut_row.name} cannot cut parts out: pick a flat bit as the cut tool "
                            f"(a V-bit can be the mark tool)")
        # Parts closer together than the bit is wide would be cut into by the
        # neighbour's outside path, so refuse instead of writing unsafe G-code.
        if c.gap + 1e-6 < c.cut_row.diameter_mm + 0.5:
            raise UserError(f"The parts were nested {c.gap:g} mm apart but the {c.cut_row.name} is "
                            f"{c.cut_row.diameter_mm:g} mm wide: nest again with a gap of at least "
                            f"{c.cut_row.diameter_mm + 1:g} mm, or pick a thinner bit")
    elif c.kerf > c.gap + 1e-6:
        # the beam runs kerf/2 outside each part and burns kerf/2 either side of that line
        raise UserError(f"The parts were nested {c.gap:g} mm apart, less than the {c.kerf:g} mm kerf: "
                        f"nest again with a gap of at least {c.kerf:g} mm, or lower the kerf")

    # Checks: a block always stops; warnings stop unless the request accepts them.
    issues = _issues(c)
    blocks = [i for i in issues if i["severity"] == "block"]
    if blocks or (issues and not body.accept_warnings):
        raise UserError(summary(issues), 422, {"issues": issues})

    rev = next_revision(data_dir, job)
    sub = rev_dirname(rev)
    folder = os.path.join(job_dir(data_dir, job.id), sub)
    os.makedirs(folder, exist_ok=True)
    base = slug(job.name, f"job{job.id}")
    stamp = datetime.now()
    meta = dict(job=job.name, job_id=job.id, revision=rev, total=len(c.result.sheets),
                material=c.mat.name if c.mat else "", thickness=c.mat.thickness_mm if c.mat else None,
                date=stamp.strftime("%Y-%m-%d %H:%M"), version=VERSION)
    try:
        if body.target == "ncstudio":
            records, w = _export_ncstudio(c, folder, base, meta)
        else:
            records, w = _export_ruida(c, body, folder, base, meta)
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)    # a failed export leaves no half revision
        raise
    created = iso(stamp)
    for r in records:
        r.update(revision=rev, dir=sub, created_at=created)
    job.exports_json = json.dumps(exports_of(job) + records)
    if job.status in ("draft", "nested", "exported"):
        job.status = "exported"
    s.commit()
    return {"files": [file_out(job.id, r) for r in records], "warnings": c.warnings + w,
            "revision": rev, "issues": issues}


def _gcode_info(meta: dict, n: int, tool, c: _Ctx) -> list[str]:
    return [
        f"JOB {meta['job']} - ID {meta['job_id']}",
        f"SHEET {n} OF {meta['total']} - REVISION {meta['revision']}",
        f"MATERIAL {meta['material']} - THICKNESS {meta['thickness']:g} mm",
        f"BIT {tool.name} - FEED {tool.feed_mm_min:g} - PLUNGE {tool.plunge_mm_min:g} - RPM {tool.rpm}"
        f" - PASS {tool.pass_depth_mm:g} - RAMP {tool.ramp_mm:g} mm",
        f"DATE {meta['date']} - Nest {meta['version']}",
    ]


def _export_ncstudio(c: _Ctx, folder: str, base: str, meta: dict) -> tuple[list[dict], list[str]]:
    from cam.ncstudio import write_ncstudio
    from cam.toolpaths import sheet_toolpaths

    tools = {c.cut_tool.id: c.cut_tool}
    if c.mark_tool is not None:
        tools[c.mark_tool.id] = c.mark_tool
    rev = meta["revision"]
    records, warnings = [], []
    for sh in c.result.sheets:
        n = sh.index + 1
        try:
            paths, w = sheet_toolpaths(sh, c.parts, c.cut_tool, c.mark_tool, c.cam)
        except ValueError as e:
            raise UserError(str(e)) from e
        warnings += [f"Sheet {n}: {x}" for x in w]
        # One file per bit, in the order the paths use them (marks first).
        order: list[str] = []
        for p in paths:
            if p.tool_id not in order:
                order.append(p.tool_id)
        for tid in order:
            tool = tools.get(tid, c.cut_tool)
            name = f"{base}_sheet{n:02d}_{slug(tool.name, 'tool' + tid)}_r{rev}.nc"
            path = os.path.join(folder, name)
            info = write_ncstudio(path, [p for p in paths if p.tool_id == tid], tool,
                                  title=f"{meta['job']} sheet {n}", sheet_w=sh.width, sheet_h=sh.height,
                                  safe_z=c.cam.safe_z, info=_gcode_info(meta, n, tool, c))
            records.append(dict(name=name, size=os.path.getsize(path), minutes=round(info.minutes, 1),
                                tool=tool.name, target="ncstudio", sheet=n))
        if not paths:
            warnings.append(f"Sheet {n}: nothing to cut")
    return records, warnings


def _export_ruida(c: _Ctx, body, folder: str, base: str, meta: dict) -> tuple[list[dict], list[str]]:
    from cam.ruida import write_ruida_dxf

    rev = meta["revision"]
    records, warnings = [], []
    for sh in c.result.sheets:
        n = sh.index + 1
        name = f"{base}_sheet{n:02d}_r{rev}.dxf"
        path = os.path.join(folder, name)
        info = {"NEST_JOB": meta["job"], "NEST_JOB_ID": str(meta["job_id"]),
                "NEST_SHEET": f"{n} of {meta['total']}", "NEST_REVISION": str(rev),
                "NEST_MATERIAL": meta["material"],
                "NEST_THICKNESS_MM": "" if meta["thickness"] is None else f"{meta['thickness']:g}",
                "NEST_KERF_MM": f"{c.kerf:g}", "NEST_DATE": meta["date"], "NEST_VERSION": meta["version"]}
        try:
            count = write_ruida_dxf(path, sh, c.parts, kerf_mm=c.kerf,
                                    include_sheet_outline=bool(body.include_sheet_outline), info=info)
        except ValueError as e:
            raise UserError(str(e)) from e
        if not count:
            warnings.append(f"Sheet {n}: nothing to cut")
        records.append(dict(name=name, size=os.path.getsize(path), minutes=None, tool=None,
                            target="ruida", sheet=n))
    return records, warnings


def export_zip(data_dir: str, job: Job, all_revisions: bool = False) -> bytes:
    """Zip of the current files (latest revision per target), or of every revision."""
    recs = exports_of(job)
    cur = _current_names(recs)
    picked = []
    for r in recs:
        if not all_revisions and r["name"] not in cur:
            continue
        try:
            picked.append((job_file_path(data_dir, job, r["name"]), r))
        except UserError:
            continue
    if not picked:
        raise not_found("Exported files")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for path, r in picked:
            z.write(path, f"{r['dir']}/{r['name']}" if all_revisions and r.get("dir") else r["name"])
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
                nest=nest, exports=files_out(job))


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

