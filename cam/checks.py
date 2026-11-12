"""Checks before export (PLAN.md Part 2, item 2.6).

pre_export_checks() looks at a nested job the way the machine will cut it and
returns a list of issues, each a dict:

    {code, severity: "block" | "warn", message, part_id, sheet, x, y}

`code` is stable (the UI translates it), `message` is plain English, `sheet`
is 1-based (None for job-wide issues), x/y are sheet mm where the problem is
(None when it has no single place).

"block" issues mean the file would be wrong or unsafe: the export refuses
them always. "warn" issues are things the operator may accept (a rounded
inside corner, a small loose part); the export needs accept_warnings for them.
"""
from __future__ import annotations

import math

from shapely.geometry import Polygon

from core.models import Part, SheetResult, placed_part

from .toolpaths import CamSettings, CamTool, _clean_ring, _polygons, sheet_toolpaths

BLOCK, WARN = "block", "warn"
SPOILBOARD_LIMIT_MM = 1.0   # deepest cut into the spoil board we allow below the sheet
SMALL_PART_MM = 50.0        # untabbed parts smaller than this (bbox short side) may come loose
GAP_MARGIN_MM = 0.5         # same rule as the export guard: gap >= bit + 0.5 mm
_MIN_LOST_MM2 = 0.5         # lost bits smaller than this are rounding noise

CODES = ("slot_too_narrow", "corner_too_tight", "hole_too_small", "small_part_no_tabs",
         "outside_bed", "too_deep", "bit_wider_than_gap", "kerf_wider_than_gap")


def _issue(code, severity, message, part_id=None, sheet=None, x=None, y=None) -> dict:
    r = (lambda v: None if v is None else round(float(v), 2))
    return dict(code=code, severity=severity, message=message, part_id=part_id, sheet=sheet, x=r(x), y=r(y))


def _fmt(v: float) -> str:
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return s or "0"


def _thick_part(piece, r: float) -> bool:
    """True when the lost piece is more than a normal rounded corner. A 90
    degree inside corner leaves a sliver whose inscribed circle has radius
    0.17 r; we flag pieces fatter than 0.25 r (corners sharper than ~75 deg,
    and every slot narrower than the bit)."""
    try:
        return not piece.buffer(-0.25 * r).is_empty
    except Exception:
        return False


def _slot_like(piece, d: float) -> bool:
    """Long thin lost piece (a slot or a narrow gap) rather than a corner."""
    try:
        rect = piece.minimum_rotated_rectangle
        xs = list(rect.exterior.coords)
        sides = sorted(math.dist(xs[i], xs[i + 1]) for i in range(2))
        return sides[1] > 2.0 * d
    except Exception:
        return False


def _lost_geometry(shape, r: float):
    """Pieces of the drawn shape the bit cannot reproduce, as
    (kind, piece) with kind "outside" (material left in a notch of the outline)
    or "hole" (material left inside a hole), plus the holes lost completely."""
    out, whole_holes = [], []
    for poly in _polygons(shape):
        ext = _clean_ring(poly.exterior.coords)
        if ext:
            e = Polygon(ext)
            closed = e.buffer(r, join_style="round").buffer(-r, join_style="round")
            for p in _polygons(closed.difference(e)):
                out.append(("outside", p))
        for hole in poly.interiors:
            hp = _clean_ring(hole.coords)
            if not hp:
                continue
            h = Polygon(hp)
            opened = h.buffer(-r, join_style="round").buffer(r, join_style="round")
            if opened.is_empty or opened.area <= 1e-6:
                whole_holes.append(h)
                continue
            for p in _polygons(h.difference(opened)):
                out.append(("hole", p))
    return out, whole_holes


def geometry_issues(sheet_no: int, part: Part, shape, tool: CamTool) -> list[dict]:
    """Inside corners, slots and holes the bit cannot cut, for one placed part."""
    r = tool.diameter_mm / 2.0
    d = _fmt(tool.diameter_mm)
    issues: list[dict] = []
    lost, whole = _lost_geometry(shape, r)
    for h in whole:
        c = h.representative_point()
        issues.append(_issue("hole_too_small", WARN,
                             f"A hole in {part.name} is smaller than the {d} mm bit and will not be cut",
                             part.id, sheet_no, c.x, c.y))
    seen = set()
    for _kind, piece in lost:
        if piece.area < _MIN_LOST_MM2 or not _thick_part(piece, r):
            continue
        code = "slot_too_narrow" if _slot_like(piece, tool.diameter_mm) else "corner_too_tight"
        if code in seen:          # one of each kind per part is enough to act on
            continue
        seen.add(code)
        c = piece.representative_point()
        msg = (f"{part.name} has a slot or gap narrower than the {d} mm bit: it will not be cut"
               if code == "slot_too_narrow" else
               f"{part.name} has an inside corner too tight for the {d} mm bit: it will come out rounded")
        issues.append(_issue(code, WARN, msg, part.id, sheet_no, c.x, c.y))
    return issues


def pre_export_checks(sheets: list[SheetResult], parts: dict[str, Part], *, target: str,
                      cut_tool: CamTool | None = None, mark_tool: CamTool | None = None,
                      cam: CamSettings | None = None, gap_mm: float | None = None,
                      bed_w: float = 0.0, bed_h: float = 0.0, kerf_mm: float = 0.0,
                      spoilboard_mm: float = SPOILBOARD_LIMIT_MM) -> list[dict]:
    """All issues for the job's sheets, blocks first. target: "ncstudio" | "ruida"."""
    issues: list[dict] = []
    has_bed = bed_w > 0 and bed_h > 0

    if target == "ruida":
        if gap_mm is not None and kerf_mm > gap_mm + 1e-6:
            issues.append(_issue("kerf_wider_than_gap", BLOCK,
                                 f"The kerf ({_fmt(kerf_mm)} mm) is wider than the {_fmt(gap_mm)} mm gap "
                                 f"between parts: nest again with a bigger gap"))
        if has_bed:
            k = max(0.0, kerf_mm) / 2.0
            for sh in sheets:
                for pl in sh.placements:
                    part = parts.get(pl.part_id)
                    if part is None:
                        continue
                    shape, _ = placed_part(part, pl)
                    b = shape.bounds
                    if b[0] - k < -1e-6 or b[1] - k < -1e-6 or b[2] + k > bed_w + 1e-6 or b[3] + k > bed_h + 1e-6:
                        issues.append(_issue("outside_bed", BLOCK,
                                             f"{part.name} on sheet {sh.index + 1} is outside the "
                                             f"{_fmt(bed_w)} x {_fmt(bed_h)} mm machine bed",
                                             part.id, sh.index + 1, b[0], b[1]))
        return _sorted(issues)

    if cut_tool is None or cam is None:
        raise ValueError("CNC checks need the cut tool and the CAM settings")
    if gap_mm is not None and gap_mm + 1e-6 < cut_tool.diameter_mm + GAP_MARGIN_MM:
        issues.append(_issue("bit_wider_than_gap", BLOCK,
                             f"The parts are {_fmt(gap_mm)} mm apart but the {cut_tool.name} is "
                             f"{_fmt(cut_tool.diameter_mm)} mm wide: nest again with a gap of at least "
                             f"{_fmt(cut_tool.diameter_mm + 1)} mm, or pick a thinner bit"))
    floor = -(cam.thickness_mm + min(max(cam.cut_through_mm, 0.0), spoilboard_mm))
    if cam.cut_through_mm > spoilboard_mm + 1e-9:
        issues.append(_issue("too_deep", BLOCK,
                             f"Cutting {_fmt(cam.cut_through_mm)} mm into the spoil board is more than the "
                             f"{_fmt(spoilboard_mm)} mm limit: lower the cut-through"))

    r = cut_tool.diameter_mm / 2.0
    tab_len = cam.tab_width_mm + cut_tool.diameter_mm
    tab_top = min(-cam.thickness_mm + cam.tab_height_mm, 0.0)
    bottom = -(cam.thickness_mm + cam.cut_through_mm)
    for sh in sheets:
        n = sh.index + 1
        paths, _ = sheet_toolpaths(sh, parts, cut_tool, mark_tool, cam)
        # bed: every bit centre position must be reachable
        if has_bed:
            for tp in paths:
                bad = next(((x, y) for x, y, _ in tp.points
                            if x < -1e-6 or y < -1e-6 or x > bed_w + 1e-6 or y > bed_h + 1e-6), None)
                if bad is not None:
                    name = parts[tp.part_id].name if tp.part_id in parts else tp.part_id
                    issues.append(_issue("outside_bed", BLOCK,
                                         f"The toolpath of {name} on sheet {n} goes outside the "
                                         f"{_fmt(bed_w)} x {_fmt(bed_h)} mm machine bed",
                                         tp.part_id, n, bad[0], bad[1]))
                    break
        # depth: nothing below the material + spoil board limit
        deepest = None
        for tp in paths:
            for x, y, z in tp.points:
                if z < floor - 1e-6 and (deepest is None or z < deepest[2]):
                    deepest = (x, y, z, tp.part_id)
        if deepest is not None and cam.cut_through_mm <= spoilboard_mm + 1e-9:
            issues.append(_issue("too_deep", BLOCK,
                                 f"Sheet {n} cuts to Z{_fmt(deepest[2])}, deeper than the material plus "
                                 f"{_fmt(spoilboard_mm)} mm of spoil board",
                                 deepest[3], n, deepest[0], deepest[1]))
        # per part (first copy on each sheet): geometry the bit cannot cut, loose parts
        seen: set[str] = set()
        for pl in sh.placements:
            part = parts.get(pl.part_id)
            if part is None or pl.part_id in seen:
                continue
            seen.add(pl.part_id)
            try:
                shape, _ = placed_part(part, pl)
            except Exception:
                continue
            if not shape.is_valid:
                shape = shape.buffer(0)
            issues += geometry_issues(n, part, shape, cut_tool)
            short = min(part.width, part.height)
            tabbed = cam.tabs and short >= cam.tab_min_part_mm and tab_top > bottom + 1e-9
            if tabbed:   # same "too small for tabs" rule as the toolpaths (outside ring = outline + 2 pi r)
                polys = _polygons(shape)
                perim = max((p.exterior.length for p in polys), default=0.0) + 2 * math.pi * r
                count = int(min(8, max(2, round(perim / 400.0))))
                tabbed = count * tab_len <= 0.5 * perim
            if not tabbed and short < max(SMALL_PART_MM, cam.tab_min_part_mm):
                c = shape.centroid
                issues.append(_issue("small_part_no_tabs", WARN,
                                     f"{part.name} ({_fmt(part.width)} x {_fmt(part.height)} mm) gets no tabs "
                                     f"and may move when it comes free: hold it down or turn tabs on",
                                     part.id, n, c.x, c.y))
    return _sorted(issues)


def _sorted(issues: list[dict]) -> list[dict]:
    return sorted(issues, key=lambda i: (i["severity"] != BLOCK, i["sheet"] or 0))


def summary(issues: list[dict], limit: int = 3) -> str:
    """One plain sentence for an error toast."""
    blocks = [i for i in issues if i["severity"] == BLOCK]
    warns = [i for i in issues if i["severity"] == WARN]
    shown = (blocks or warns)[:limit]
    more = len(blocks or warns) - len(shown)
    text = "; ".join(i["message"] for i in shown) + (f" (and {more} more)" if more > 0 else "")
    if blocks:
        return f"Export stopped by {len(blocks)} problem(s): {text}"
    return (f"Export needs a look at {len(warns)} warning(s): {text}. "
            f"Tick 'accept warnings' to make the files anyway")


__all__ = ["pre_export_checks", "geometry_issues", "summary", "CODES", "BLOCK", "WARN",
           "SPOILBOARD_LIMIT_MM", "SMALL_PART_MM"]
