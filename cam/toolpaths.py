"""2D router toolpaths for one nested sheet (PLAN.md section 4.5).

What this module does, in plain words:
- Every placed part is cut in three kinds of moves:
  marks (engrave/score lines, followed exactly on the line),
  holes (the bit runs inside the hole, offset inward by the bit radius),
  outside profiles (the bit runs outside the part, offset outward by the radius).
- Profiles are cut in several depth levels down to just below the material
  (into the spoil board by cut_through_mm). Marks are one shallow level.
- Levels deeper than the tab top on an outside profile leave small tabs so the part does not
  move when it comes free.
- Entry moves (tool.ramp_mm > 0): instead of plunging straight down, the bit
  ramps down along the path in a short zigzag at the start point (so every
  level starts and ends there), or, on rings too short for that (small holes),
  in a helix that follows the ring itself, so it never leaves the hole.
- The result is a flat, already ordered list of ToolPath objects. The G-code
  writer only has to follow them.

Coordinates: mm, sheet frame, (0,0) bottom-left, Y up, Z0 = material top,
Z negative into the material.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from shapely.geometry import LinearRing, LineString, MultiLineString, MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry

from core.models import Part, SheetResult, placed_part

_EPS = 1e-6


@dataclass
class CamTool:
    id: str
    name: str
    diameter_mm: float
    kind: str = "flat"              # flat | ball | vbit
    angle_deg: float | None = None  # vbit only
    feed_mm_min: float = 3000
    plunge_mm_min: float = 800
    rpm: int = 18000
    pass_depth_mm: float = 3.0      # max depth per pass
    ramp_mm: float = 0.0            # ramp entry length along the path; 0 = straight plunge


@dataclass
class CamSettings:
    thickness_mm: float             # material thickness
    cut_through_mm: float = 0.3     # go this far into the spoil board
    safe_z: float = 5.0
    climb: bool = True
    tabs: bool = True
    tab_width_mm: float = 6.0
    tab_height_mm: float = 1.5      # material left under the tab
    tab_min_part_mm: float = 0.0    # parts whose bbox min side is smaller get NO tabs (0 = all)
    engrave_depth_mm: float = 0.5   # for marks on layer engrave/score
    holes_first: bool = True
    lead_in: str = "auto"           # outside start: "auto" (nearest bottom-left) | "longest_edge_mid"
    ramp_max_angle_deg: float = 10.0  # steepest ramp; steeper steps get more zigzag legs / helix turns


LEAD_INS = ("auto", "longest_edge_mid")


@dataclass
class ToolPath:
    """One continuous cut at one depth level, already ordered.
    Profiles are closed (first point == last point); marks are open lines."""
    tool_id: str
    points: list[tuple[float, float, float]]   # x, y, z in mm, z <= 0 while cutting
    kind: str                                  # "inside" | "outside" | "mark"
    part_id: str
    copy: int


# ---------------------------------------------------------------- helpers

def _dist(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _polygons(geom: BaseGeometry | None) -> list[Polygon]:
    """All non-empty polygons inside a geometry (buffer can return a
    MultiPolygon, a GeometryCollection, or nothing at all)."""
    if geom is None or geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom]
    if isinstance(geom, MultiPolygon) or hasattr(geom, "geoms"):
        out: list[Polygon] = []
        for g in geom.geoms:
            out.extend(_polygons(g))
        return out
    return []


def _clean_ring(coords) -> list[tuple[float, float]]:
    """Ring vertices without the closing point and without repeated points.
    Returns [] if fewer than 3 distinct points are left (degenerate)."""
    pts: list[tuple[float, float]] = []
    for c in coords:
        p = (float(c[0]), float(c[1]))
        if not pts or _dist(pts[-1], p) > _EPS:
            pts.append(p)
    while len(pts) > 1 and _dist(pts[0], pts[-1]) <= _EPS:
        pts.pop()
    return pts if len(pts) >= 3 else []


def _orient(pts: list[tuple[float, float]], ccw: bool) -> list[tuple[float, float]]:
    """Return the ring vertices running counter-clockwise (ccw=True) or clockwise."""
    is_ccw = LinearRing(pts).is_ccw
    return pts if is_ccw == ccw else pts[::-1]


def _start_at(pts: list[tuple[float, float]], i: int) -> list[tuple[float, float]]:
    """Rotate the ring so it starts at vertex i, and close it."""
    r = pts[i:] + pts[:i]
    return r + [r[0]]


def _closest_index(pts, target) -> int:
    return min(range(len(pts)), key=lambda i: (_dist(pts[i], target), i))


def _offset_rings(poly: Polygon, distance: float) -> list[list[tuple[float, float]]]:
    """Offset a hole-free polygon by distance (round corners) and return the
    exterior of every resulting piece. Inward offsets of narrow shapes can split
    into several pieces, or vanish (empty list)."""
    try:
        res = poly.buffer(distance, join_style="round")
    except Exception:  # degenerate input: treat as nothing to cut
        return []
    rings = []
    for p in _polygons(res):
        if p.area <= _EPS:
            continue
        pts = _clean_ring(p.exterior.coords)
        if pts:
            rings.append(pts)
    return rings


def _depth_levels(total: float, pass_depth: float) -> list[float]:
    """Evenly spaced Z levels from the first pass down to -total."""
    if total <= 0:
        return []
    pd = pass_depth if pass_depth > 0 else total
    n = max(1, math.ceil(total / pd - 1e-9))
    return [-total * (i + 1) / n for i in range(n)]


def _fmt(v: float) -> str:
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return s or "0"


def _with_tabs(ring: list[tuple[float, float]], level: float, tab_z: float,
               tab_len: float, count: int) -> list[tuple[float, float, float]]:
    """Closed 2D ring -> 3D points at `level`, lifted to `tab_z` along `count`
    tabs. Tab centres are evenly spaced by arc length, the first half a spacing
    from the start point. Exact points are inserted at every tab start/end, and
    the tool steps straight up/down there (same XY, two points)."""
    cum = [0.0]
    for a, b in zip(ring, ring[1:]):
        cum.append(cum[-1] + _dist(a, b))
    perim = cum[-1]
    spacing = perim / count
    intervals = [(spacing * (k + 0.5) - tab_len / 2, spacing * (k + 0.5) + tab_len / 2)
                 for k in range(count)]
    splits = sorted(s for iv in intervals for s in iv)

    # 2D points tagged with arc length, with the tab ends inserted
    tagged: list[tuple[float, float, float]] = [(ring[0][0], ring[0][1], 0.0)]
    j = 0
    for i in range(len(ring) - 1):
        a, b, s0, s1 = ring[i], ring[i + 1], cum[i], cum[i + 1]
        while j < len(splits) and splits[j] < s1 - _EPS:
            s = splits[j]
            if s > s0 + _EPS:
                t = (s - s0) / (s1 - s0)
                tagged.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, s))
            else:  # split falls on vertex a, which is already in the list
                tagged[-1] = (tagged[-1][0], tagged[-1][1], s)
            j += 1
        tagged.append((b[0], b[1], s1))
    # a split that lands exactly on a vertex end s1 is handled on the next segment as s0

    starts = [iv[0] for iv in intervals]
    ends = [iv[1] for iv in intervals]
    out: list[tuple[float, float, float]] = []
    for x, y, s in tagged:
        if any(abs(s - st) <= 1e-6 for st in starts):
            out += [(x, y, level), (x, y, tab_z)]
        elif any(abs(s - en) <= 1e-6 for en in ends):
            out += [(x, y, tab_z), (x, y, level)]
        elif any(st < s < en for st, en in intervals):
            out.append((x, y, tab_z))
        else:
            out.append((x, y, level))
    return out


def _walk(closed: list[tuple[float, float]], length: float) -> list[tuple[float, float]]:
    """Points along a closed ring from its start, for `length` mm of arc
    (wrapping round as often as needed). The last point is interpolated."""
    out = [closed[0]]
    left = length
    if left <= _EPS:
        return out
    while True:
        for a, b in zip(closed, closed[1:]):
            d = _dist(a, b)
            if d <= _EPS:
                continue
            if d >= left - _EPS:
                t = min(1.0, left / d)
                out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
                return out
            out.append(b)
            left -= d


def _slope(pts2: list[tuple[float, float]], z_from: float, z_to: float) -> list[tuple[float, float, float]]:
    """2D polyline -> 3D, Z falling linearly with arc length from z_from to z_to."""
    cum = [0.0]
    for a, b in zip(pts2, pts2[1:]):
        cum.append(cum[-1] + _dist(a, b))
    total = cum[-1] or 1.0
    return [(x, y, z_from + (z_to - z_from) * c / total) for (x, y), c in zip(pts2, cum)]


def _entry(closed: list[tuple[float, float]], z_from: float, z_to: float, ramp_mm: float,
           max_angle_deg: float, max_leg: float | None) -> tuple[list[tuple[float, float, float]], str]:
    """Entry move from (start, z_from) down to (start, z_to), returning
    (points, mode). Both ends are the ring's start point, so every level of a
    ring starts and ends in the same place and the bit never lifts between
    levels. Z never goes below z_to.

    zigzag: forward along the ring for ramp_mm / 2 and back, repeated until
            the slope is no steeper than max_angle_deg (the footprint stays
            ramp_mm / 2 long, on the start edge, clear of the tabs).
    helix:  rings shorter than 2 x ramp_mm (small holes): whole turns of the
            ring itself while going down; the bit stays on the cut path, so
            inside the hole.
    plunge: no ramp (ramp_mm = 0, nothing to go down, or no room)."""
    dz = z_from - z_to
    if ramp_mm <= 0 or dz <= 1e-9:
        return [(closed[0][0], closed[0][1], z_to)], "plunge"
    tan_max = math.tan(math.radians(min(max(max_angle_deg, 1.0), 45.0)))
    perim = sum(_dist(a, b) for a, b in zip(closed, closed[1:]))
    if perim <= _EPS:
        return [(closed[0][0], closed[0][1], z_to)], "plunge"
    if perim >= 2 * ramp_mm:
        leg = ramp_mm / 2.0
        if max_leg is not None:
            leg = min(leg, max_leg)
        if leg >= 1.0:
            fwd = _walk(closed, leg)
            back = fwd[::-1]
            legs = max(2, 2 * math.ceil(dz / (2 * leg * tan_max) - 1e-9))
            pts2 = list(fwd)
            for k in range(1, legs):
                pts2 += (back if k % 2 else fwd)[1:]
            return _slope(pts2, z_from, z_to), "zigzag"
        if max_leg is not None:          # tabs too close to the start: plain plunge
            return [(closed[0][0], closed[0][1], z_to)], "plunge"
    turns = max(1, math.ceil(dz / (perim * tan_max) - 1e-9))
    pts2 = list(closed)
    for _ in range(turns - 1):
        pts2 += closed[1:]
    return _slope(pts2, z_from, z_to), "helix"


def _longest_edge_mid(pts: list[tuple[float, float]]) -> tuple[list[tuple[float, float]], int]:
    """Insert the middle of the longest straight edge of a (not closed) ring
    as a vertex; return the new ring and that vertex's index."""
    n = len(pts)
    i = max(range(n), key=lambda k: (_dist(pts[k], pts[(k + 1) % n]), -k))
    a, b = pts[i], pts[(i + 1) % n]
    mid = ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
    return pts[:i + 1] + [mid] + pts[i + 1:], i + 1


def _warn(warnings: list[str], msg: str) -> None:
    if msg not in warnings:
        warnings.append(msg)


# ---------------------------------------------------------------- main entry

@dataclass
class _Ring:
    """One closed ring to profile (before ordering)."""
    pts: list[tuple[float, float]]     # oriented, not closed
    kind: str                          # inside | outside
    part_id: str
    copy: int
    tabs: bool = False
    fixed_start: int | None = None     # outside: vertex closest to sheet (0,0)


def sheet_toolpaths(sheet: SheetResult, parts: dict[str, Part], cut_tool: CamTool,
                    mark_tool: CamTool | None, s: CamSettings) -> tuple[list[ToolPath], list[str]]:
    """Ordered toolpaths for one sheet, plus plain-English warnings.

    Order: all marks, then all holes, then all outside profiles (with
    holes_first=False: marks, then part by part, each part's holes before its
    outside). Inside each group: nearest neighbour starting from (0,0)."""
    warnings: list[str] = []
    if s.lead_in not in LEAD_INS:
        raise ValueError(f"Unknown lead-in {s.lead_in!r}: use one of {', '.join(LEAD_INS)}")
    r = cut_tool.diameter_mm / 2.0
    mtool = mark_tool or cut_tool
    total = s.thickness_mm + s.cut_through_mm
    levels = _depth_levels(total, cut_tool.pass_depth_mm)
    if not levels:
        _warn(warnings, "Material thickness is 0, nothing to cut")
    # climb milling with a clockwise spindle: outside clockwise, inside counter-clockwise
    outside_ccw = not s.climb
    inside_ccw = s.climb

    # tab top: -thickness + tab_height (never above 0). Every level that goes
    # deeper than the tab top is lifted to it along the tabs, so the tab keeps
    # its full height however the passes fall.
    tab_z = None
    if s.tabs and levels:
        tab_z = min(-s.thickness_mm + s.tab_height_mm, 0.0)
        if tab_z <= levels[-1] + 1e-9:
            tab_z = None   # a tab at the bottom level is no tab at all
    tab_len = s.tab_width_mm + cut_tool.diameter_mm

    marks: list[tuple[list[tuple[float, float]], str, int]] = []
    rings_by_part: list[tuple[str, int, list[_Ring], list[_Ring]]] = []   # (part, copy, holes, outs)
    placed_polys: list[list[Polygon]] = []                                # same index as rings_by_part

    for pl in sheet.placements:
        part = parts.get(pl.part_id)
        if part is None:
            _warn(warnings, f"Part {pl.part_id} is missing, skipped")
            continue
        try:
            shape, mlines = placed_part(part, pl)
        except Exception:
            _warn(warnings, f"Part {part.name} has broken geometry, skipped")
            continue
        if not shape.is_valid:
            shape = shape.buffer(0)
        polys = _polygons(shape)
        if not polys:
            _warn(warnings, f"Part {part.name} has no area, skipped")
        big_enough = min(part.width, part.height) >= s.tab_min_part_mm
        holes: list[_Ring] = []
        outs: list[_Ring] = []
        for poly in polys:
            for hole in poly.interiors:
                hpts = _clean_ring(hole.coords)
                if not hpts:
                    continue
                rings = _offset_rings(Polygon(hpts), -r)
                if not rings:
                    _warn(warnings, f"A hole in {part.name} is smaller than the "
                                    f"{_fmt(cut_tool.diameter_mm)} mm bit, skipped")
                for rp in rings:
                    holes.append(_Ring(_orient(rp, inside_ccw), "inside", pl.part_id, pl.copy))
            epts = _clean_ring(poly.exterior.coords)
            if not epts:
                continue
            for rp in _offset_rings(Polygon(epts), r):
                pts = _orient(rp, outside_ccw)
                if s.lead_in == "longest_edge_mid":
                    pts, start = _longest_edge_mid(pts)
                else:
                    start = _closest_index(pts, (0.0, 0.0))
                outs.append(_Ring(pts, "outside", pl.part_id, pl.copy,
                                  tabs=tab_z is not None and big_enough, fixed_start=start))
        for m in mlines:
            lines = list(m.geoms) if isinstance(m, MultiLineString) else [m]
            for ln in lines:
                if not isinstance(ln, LineString) or ln.is_empty:
                    continue
                pts = []
                for c in ln.coords:
                    p = (float(c[0]), float(c[1]))
                    if not pts or _dist(pts[-1], p) > _EPS:
                        pts.append(p)
                if len(pts) >= 2:
                    marks.append((pts, pl.part_id, pl.copy))
        rings_by_part.append((pl.part_id, pl.copy, holes, outs))
        placed_polys.append(polys)

    # A part nested inside another part's hole must be cut before that hole:
    # once the hole is cut through, its slug (with the small part on it) is loose.
    # depth = how many placed holes hold the part; deeper parts are cut first.
    hole_areas = [[Polygon(h) for p in ps for h in p.interiors] for ps in placed_polys]
    depth = []
    for i, ps in enumerate(placed_polys):
        pt = ps[0].representative_point() if ps else None
        depth.append(0 if pt is None else sum(
            1 for j, hs in enumerate(hole_areas) if j != i and any(h.contains(pt) for h in hs)))

    paths: list[ToolPath] = []
    pos = (0.0, 0.0)

    def emit_ring(ring: _Ring, pos):
        """Add all depth levels of one ring; return the new tool position."""
        i = ring.fixed_start if ring.fixed_start is not None else _closest_index(ring.pts, pos)
        closed = _start_at(ring.pts, i)
        count = 0
        if ring.tabs:
            perim = sum(_dist(a, b) for a, b in zip(closed, closed[1:]))
            count = int(min(8, max(2, round(perim / 400.0))))
            if count * tab_len > 0.5 * perim:
                _warn(warnings, f"Part {parts[ring.part_id].name} is too small for tabs, "
                                f"cut without tabs (hold it down)")
                count = 0
        # the zigzag ramp must end before the first tab starts (never ramp through a tab)
        max_leg = None
        if count:
            perim = sum(_dist(a, b) for a, b in zip(closed, closed[1:]))
            max_leg = perim / count * 0.5 - tab_len / 2.0 - 0.5
        z_prev = 0.0
        for k, z in enumerate(levels):
            if count and z < tab_z - 1e-9:
                loop = _with_tabs(closed, z, tab_z, tab_len, count)
            else:
                loop = [(x, y, z) for x, y in closed]
            entry, _mode = _entry(closed, z_prev, z, cut_tool.ramp_mm, s.ramp_max_angle_deg, max_leg)
            pts3 = entry + loop[1:] if len(entry) > 1 else loop
            paths.append(ToolPath(cut_tool.id, pts3, ring.kind, ring.part_id, ring.copy))
            z_prev = z
        return closed[0]

    def start_dist(ring: _Ring, pos) -> float:
        if ring.fixed_start is not None:
            return _dist(ring.pts[ring.fixed_start], pos)
        return min(_dist(p, pos) for p in ring.pts)

    def nearest_rings(rings: list[_Ring], pos):
        left = list(rings)
        while left:
            j = min(range(len(left)), key=lambda q: (start_dist(left[q], pos), q))
            pos = emit_ring(left.pop(j), pos)
        return pos

    # 1. marks, nearest neighbour from (0,0); a line may be cut from either end
    left = list(marks)
    pos = (0.0, 0.0)
    # never deeper than the profiles (PLAN 7: Z >= -(thickness + cut_through))
    me = -min(abs(s.engrave_depth_mm), total) if total > 0 else -abs(s.engrave_depth_mm)
    while left:
        def md(q):
            p = left[q][0]
            return min(_dist(p[0], pos), _dist(p[-1], pos))
        j = min(range(len(left)), key=lambda q: (md(q), q))
        pts, pid, cp = left.pop(j)
        if _dist(pts[-1], pos) < _dist(pts[0], pos):
            pts = pts[::-1]
        paths.append(ToolPath(mtool.id, [(x, y, me) for x, y in pts], "mark", pid, cp))
        pos = pts[-1]

    if s.holes_first:
        # 2. all holes, 3. all outside profiles; each group from (0,0).
        # Parts sitting in holes (deepest first) are finished before the hole around them.
        for d in sorted(set(depth), reverse=True):
            grp = [rp for k, rp in enumerate(rings_by_part) if depth[k] == d]
            nearest_rings([h for _, _, hs, _ in grp for h in hs], (0.0, 0.0))
            nearest_rings([o for _, _, _, os_ in grp for o in os_], (0.0, 0.0))
    else:
        # part by part (nearest part first by its outside start), holes then outside;
        # parts sitting in holes (deepest first) before the part around them
        left_parts = list(zip(depth, rings_by_part))
        pos = (0.0, 0.0)

        def pd(q):
            dq, (_, _, hs, os_) = left_parts[q]
            rs = os_ or hs
            return (-dq, min(start_dist(x, pos) for x in rs) if rs else float("inf"))
        while left_parts:
            j = min(range(len(left_parts)), key=lambda q: (pd(q), q))
            _, (_, _, hs, os_) = left_parts.pop(j)
            pos = nearest_rings(hs, pos)
            pos = nearest_rings(os_, pos)

    return paths, warnings


__all__ = ["CamTool", "CamSettings", "ToolPath", "sheet_toolpaths", "LEAD_INS"]
