"""Read SVG and DXF cut files into Parts (PLAN.md section 4.2).

Flow for both formats:
  1. read the file into flat polylines in millimetres, Y up
     (each one either closed = a ring, or open = a line);
  2. join open pieces end to end into chains; a chain whose ends meet is a ring;
  3. group rings into parts by how deep they sit inside each other
     (even depth = outer edge, odd depth = hole, depth 2 = island = new part);
  4. attach open lines to the part they lie in, as marks;
  5. move each part to (0, 0), name and number it.

Every problem the user can cause (bad file, nothing closed) raises
ImportError_ with a plain message. No raw stack trace ever reaches the user.
"""
from __future__ import annotations

import io
import math
import re
import uuid
from dataclasses import dataclass, field
from pathlib import PurePath

import numpy as np
from scipy.spatial import cKDTree
from shapely import affinity, make_valid
from shapely.geometry import LineString, MultiPolygon, Polygon
from shapely.geometry.polygon import orient
from shapely.ops import unary_union

from core.models import Part

PX_TO_MM = 25.4 / 96.0          # SVG user units are px at 96 ppi
JOIN_TOL_MM = 0.02              # endpoints closer than this are the same point
MIN_RING_AREA_MM2 = 1.0         # smaller closed shapes are dust, dropped
SCORE_BLUE = "#0000ff"          # SVG stroke colour that means "score" instead of "engrave"

# DXF $INSUNITS code -> (mm per drawing unit, unit name for the report)
DXF_UNITS = {1: (25.4, "inch"), 2: (304.8, "ft"), 4: (1.0, "mm"), 5: (10.0, "cm"), 6: (1000.0, "m")}
DEFAULT_UNITS = {"mm": 1.0, "inch": 25.4, "in": 25.4, "cm": 10.0, "m": 1000.0, "px": PX_TO_MM}

# DXF entity types we turn into geometry
DXF_SHAPES = {"LWPOLYLINE", "POLYLINE", "LINE", "ARC", "CIRCLE", "ELLIPSE", "SPLINE"}
DXF_TEXT = {"TEXT", "MTEXT", "ATTRIB", "ATTDEF"}


class ImportError_(ValueError):
    """A problem with the user's file. The message is shown to the user as is."""


@dataclass
class ImportReport:
    parts: list[Part]
    warnings: list[str] = field(default_factory=list)
    units: str = "mm"


@dataclass
class _Piece:
    """One flattened polyline in mm, Y up, before grouping."""
    pts: list[tuple[float, float]]
    closed: bool
    layer: str = "engrave"      # only used for open lines: engrave | score


# ----------------------------------------------------------------------------
# Public entry point
# ----------------------------------------------------------------------------

def import_file(data: bytes, filename: str, *, tolerance_mm: float = 0.05,
                default_units: str = "mm") -> ImportReport:
    """Read one SVG or DXF file (given as bytes) and return its parts."""
    ext = PurePath(filename or "").suffix.lower()
    if ext not in (".svg", ".dxf"):
        raise ImportError_("Only SVG or DXF files")
    if not data or not data.strip():
        raise ImportError_(f"{filename} is empty")
    tol = max(float(tolerance_mm), 0.001)
    warnings: list[str] = []
    try:
        if ext == ".svg":
            pieces, units = _read_svg(data, tol, warnings)
        else:
            pieces, units = _read_dxf(data, tol, default_units, warnings)
        parts = _build_parts(pieces, PurePath(filename).stem, filename, warnings)
    except ImportError_:
        raise
    except Exception:   # any library failure means the file is broken for us
        kind = "SVG" if ext == ".svg" else "DXF"
        raise ImportError_(f"Could not read {filename}: it does not look like a valid {kind} file") from None
    if not parts:
        raise ImportError_("No closed shapes found")
    return ImportReport(parts=parts, warnings=warnings, units=units)


# ----------------------------------------------------------------------------
# SVG
# ----------------------------------------------------------------------------

def _read_svg(data: bytes, tol_mm: float, warnings: list[str]) -> tuple[list[_Piece], str]:
    """Flatten every visible shape of an SVG into mm pieces (Y flipped to up)."""
    import svgelements as se

    try:
        doc = se.SVG.parse(io.BytesIO(data), ppi=96)
    except Exception:
        raise ImportError_("Could not read the SVG: the file is damaged or not an SVG") from None
    units = _svg_units(data)
    px_to_mm = _svg_px_to_mm(units)
    tol_px = tol_mm / px_to_mm
    pieces: list[_Piece] = []
    texts = images = 0
    for el in doc.elements():
        if isinstance(el, se.Text):
            texts += 1
        elif isinstance(el, se.Image):
            images += 1
        elif isinstance(el, se.Shape):
            pieces.extend(_svg_shape_pieces(el, tol_px, px_to_mm))
    if texts:
        warnings.append(f"{texts} text item(s) skipped: text must be converted to outlines")
    if images:
        warnings.append(f"{images} image(s) skipped: pictures cannot be cut, use vector shapes")
    return pieces, units


def _svg_px_to_mm(units: str) -> float:
    """mm per px. 25.4 / 96 in general; for files sized in mm/cm we use the exact
    inverse of the factor svgelements used to turn mm into px (it rounds 1/25.4
    to 0.0393701), so a 50 mm rectangle comes back as exactly 50 mm."""
    import svgelements as se

    if units in ("mm", "cm"):
        return 1.0 / se.Length("1mm").value(ppi=96)
    return PX_TO_MM


def _svg_shape_pieces(el, tol_px: float, px_to_mm: float) -> list[_Piece]:
    """Turn one SVG shape into pieces. Invisible shapes (no fill, no stroke) give none."""
    import svgelements as se

    has_fill = el.fill is not None and el.fill.value is not None
    has_stroke = el.stroke is not None and el.stroke.value is not None
    if not has_fill and not has_stroke:
        return []
    layer = "score" if has_stroke and el.stroke.hex[:7].lower() == SCORE_BLUE else "engrave"
    path = abs(se.Path(el))     # abs() = copy with all transforms applied (reify)
    out = []
    for sub in path.as_subpaths():
        pts, closed = _flatten_svg_subpath(sub, tol_px)
        if len(pts) < 2:
            continue
        # A filled shape without a stroke is drawn closed by SVG even if the
        # path never says Z, so treat it as closed. A stroked open path is a line.
        if not closed and has_fill and not has_stroke and len(pts) >= 3:
            closed = True
        mm = [(x * px_to_mm, -y * px_to_mm) for x, y in pts]   # px -> mm, flip Y up
        out.append(_Piece(mm, closed, layer))
    return out


def _flatten_svg_subpath(sub, tol_px: float) -> tuple[list[tuple[float, float]], bool]:
    """Sample one subpath into points with chord error <= tol_px. Returns (points, closed)."""
    import svgelements as se

    pts: list[tuple[float, float]] = []
    closed = False
    for seg in sub:
        if isinstance(seg, se.Move):
            if seg.end is not None:
                pts.append((seg.end.x, seg.end.y))
            continue
        if isinstance(seg, se.Close):
            closed = True
            continue
        if seg.start is None or seg.end is None:
            continue
        if not pts:
            pts.append((seg.start.x, seg.start.y))
        n = _svg_segment_steps(seg, tol_px)
        if n <= 1:
            pts.append((seg.end.x, seg.end.y))
        else:
            ts = np.linspace(0.0, 1.0, n + 1)[1:]
            for p in seg.npoint(ts):
                pts.append((float(p[0]), float(p[1])))
    return _dedupe(pts), closed


def _svg_segment_steps(seg, tol_px: float) -> int:
    """How many straight steps a curve segment needs so the chord error stays under tol."""
    import svgelements as se

    if isinstance(seg, se.Line):
        return 1
    if isinstance(seg, se.CubicBezier):
        p = [np.array([q.x, q.y]) for q in (seg.start, seg.control1, seg.control2, seg.end)]
        m = max(np.linalg.norm(p[0] - 2 * p[1] + p[2]), np.linalg.norm(p[1] - 2 * p[2] + p[3]))
        return _steps_from_second_diff(6.0 * m, tol_px)
    if isinstance(seg, se.QuadraticBezier):
        p = [np.array([q.x, q.y]) for q in (seg.start, seg.control, seg.end)]
        return _steps_from_second_diff(2.0 * np.linalg.norm(p[0] - 2 * p[1] + p[2]), tol_px)
    if isinstance(seg, se.Arc):
        r = max(abs(seg.rx), abs(seg.ry), 1e-9)
        return _arc_steps(r, abs(seg.sweep), tol_px)
    return max(2, int(math.ceil(seg.length() / max(tol_px * 4, 1e-6))))   # unknown kind: dense sampling


def _steps_from_second_diff(max_second_deriv: float, tol: float) -> int:
    """Uniform steps for a polynomial curve: error <= |B''| / (8 n^2)."""
    n = math.ceil(math.sqrt(max_second_deriv / (8.0 * tol))) if max_second_deriv > 0 else 1
    return int(min(max(n, 1), 2000))


def _arc_steps(radius: float, sweep_rad: float, tol: float) -> int:
    """Steps for a circular arc so the sagitta of each chord is <= tol."""
    if tol >= radius:
        return max(2, int(math.ceil(sweep_rad / (math.pi / 2))))
    step = 2.0 * math.acos(1.0 - tol / radius)
    return int(min(max(math.ceil(sweep_rad / step), 2), 4000))


def _svg_units(data: bytes) -> str:
    """Report the unit written on the root <svg width="...">: mm, inch, cm or px."""
    m = re.search(rb"<svg\b[^>]*?\swidth\s*=\s*[\"']\s*[\d.]+\s*([a-zA-Z%]*)", data[:4096], re.S)
    unit = (m.group(1).decode("ascii", "ignore").lower() if m else "")
    return {"mm": "mm", "in": "inch", "cm": "cm"}.get(unit, "px")


# ----------------------------------------------------------------------------
# DXF
# ----------------------------------------------------------------------------

def _read_dxf(data: bytes, tol_mm: float, default_units: str,
              warnings: list[str]) -> tuple[list[_Piece], str]:
    """Flatten the modelspace of a DXF into mm pieces (DXF is already Y up)."""
    doc = _open_dxf(data)
    code = int(doc.header.get("$INSUNITS", 0) or 0)
    if code in DXF_UNITS:
        scale, units = DXF_UNITS[code]
    else:
        units = default_units if default_units in DEFAULT_UNITS else "mm"
        scale = DEFAULT_UNITS[units]
        units = "inch" if units == "in" else units
    pieces: list[_Piece] = []
    skipped: dict[str, int] = {}
    texts = 0
    for ent in _dxf_entities(doc.modelspace()):
        kind = ent.dxftype()
        if kind in DXF_TEXT:
            texts += 1
            continue
        if kind not in DXF_SHAPES or (kind == "POLYLINE" and not ent.is_2d_polyline):
            skipped[kind] = skipped.get(kind, 0) + 1
            continue
        piece = _dxf_entity_piece(ent, tol_mm / scale, scale)
        if piece is not None:
            pieces.append(piece)
    if texts:
        warnings.append(f"{texts} text item(s) skipped: text must be converted to outlines")
    for kind, n in sorted(skipped.items()):
        warnings.append(f"{n} {kind} item(s) skipped: not a supported shape")
    return pieces, units


def _open_dxf(data: bytes):
    """Read DXF bytes: try utf-8, then Thai cp874, then latin-1; last resort ezdxf recover."""
    import ezdxf
    from ezdxf import recover

    for enc in ("utf-8", "cp874", "latin-1"):
        try:
            return ezdxf.read(io.StringIO(data.decode(enc)))
        except UnicodeDecodeError:
            continue
        except Exception:
            break
    try:
        doc, _auditor = recover.read(io.BytesIO(data))
        return doc
    except Exception:
        raise ImportError_("Could not read the DXF: the file is damaged or not a DXF") from None


def _dxf_entities(space):
    """All entities of a layout with block INSERTs exploded (nested blocks too)."""
    from ezdxf import disassemble

    return disassemble.recursive_decompose(space)


def _dxf_entity_piece(ent, tol_units: float, scale: float) -> _Piece | None:
    """Flatten one DXF entity (arcs, bulges, splines) into a mm piece."""
    from ezdxf import path as ezpath

    p = ezpath.make_path(ent)
    pts = [(v.x * scale, v.y * scale) for v in p.flattening(tol_units, segments=4)]
    pts = _dedupe(pts)
    if len(pts) < 2:
        return None
    closed = bool(ent.dxftype() == "CIRCLE" or getattr(ent, "closed", False)
                  or (ent.dxftype() == "POLYLINE" and ent.is_closed)
                  or (ent.dxftype() == "ELLIPSE" and _ellipse_is_full(ent))
                  or (ent.dxftype() == "SPLINE" and ent.closed))
    return _Piece(pts, closed)


def _ellipse_is_full(ent) -> bool:
    """True if a DXF ELLIPSE goes all the way round."""
    span = (ent.dxf.end_param - ent.dxf.start_param) % (2 * math.pi)
    return span < 1e-6 or abs(span - 2 * math.pi) < 1e-6


# ----------------------------------------------------------------------------
# Shared: chains, rings, parts
# ----------------------------------------------------------------------------

def _dedupe(pts: list[tuple[float, float]], eps: float = 1e-9) -> list[tuple[float, float]]:
    """Drop consecutive duplicate points."""
    out: list[tuple[float, float]] = []
    for p in pts:
        if not out or abs(p[0] - out[-1][0]) > eps or abs(p[1] - out[-1][1]) > eps:
            out.append(p)
    return out


def _close_enough(a, b, tol: float = JOIN_TOL_MM) -> bool:
    return math.hypot(a[0] - b[0], a[1] - b[1]) <= tol


def _join_chains(pieces: list[_Piece]) -> list[_Piece]:
    """Join open pieces whose endpoints touch (within 0.02 mm) into longer chains.

    A chain whose two ends meet becomes closed. Closed pieces pass through.
    """
    closed = [p for p in pieces if p.closed]
    open_ = [p for p in pieces if not p.closed]
    for p in open_:                      # a single piece drawn back to its start is closed
        if len(p.pts) >= 3 and _close_enough(p.pts[0], p.pts[-1]):
            p.closed = True
    closed += [p for p in open_ if p.closed]
    open_ = [p for p in open_ if not p.closed]
    if not open_:
        return closed

    ends = np.array([pt for p in open_ for pt in (p.pts[0], p.pts[-1])])   # end k belongs to piece k // 2
    tree = cKDTree(ends)
    used = [False] * len(open_)

    def take_next(point, current: int):
        """Find an unused piece with an end at `point`; return its points oriented to start there."""
        for k in sorted(tree.query_ball_point(point, JOIN_TOL_MM)):
            i = k // 2
            if used[i] or i == current:
                continue
            used[i] = True
            pts = open_[i].pts
            return pts if k % 2 == 0 else pts[::-1]
        return None

    for i, piece in enumerate(open_):
        if used[i]:
            continue
        used[i] = True
        chain = list(piece.pts)
        for _ in range(2):               # grow at the tail, then flip and grow the other end
            while not _close_enough(chain[0], chain[-1]) or len(chain) < 3:
                nxt = take_next(chain[-1], i)
                if nxt is None:
                    break
                chain.extend(nxt[1:])
            if _close_enough(chain[0], chain[-1]) and len(chain) >= 4:
                break
            chain.reverse()
        is_closed = len(chain) >= 4 and _close_enough(chain[0], chain[-1])
        closed.append(_Piece(chain, is_closed, piece.layer))
    return closed


def _rings_to_polygons(rings: list[_Piece]) -> list[Polygon]:
    """Make valid filled polygons (outer edge only) from closed rings; drop dust."""
    polys: list[Polygon] = []
    for r in rings:
        if len(r.pts) < 3:
            continue
        geom = make_valid(Polygon(r.pts))
        for g in getattr(geom, "geoms", [geom]):
            if isinstance(g, Polygon) and g.area >= MIN_RING_AREA_MM2:
                polys.append(Polygon(g.exterior))
            elif isinstance(g, MultiPolygon):
                polys.extend(Polygon(q.exterior) for q in g.geoms if q.area >= MIN_RING_AREA_MM2)
    return polys


def _inside(inner: Polygon, outer: Polygon) -> bool:
    """True if ring `inner` lies inside ring `outer` (touching edges allowed)."""
    a, b = inner.bounds, outer.bounds
    if a[0] < b[0] - 1e-6 or a[1] < b[1] - 1e-6 or a[2] > b[2] + 1e-6 or a[3] > b[3] + 1e-6:
        return False
    if outer.covers(inner):
        return True
    return outer.intersection(inner).area > 0.99 * inner.area


def _group_rings(polys: list[Polygon]) -> list[Polygon]:
    """Turn nested rings into part polygons by containment depth.

    Sorted big to small, each ring's parent is the smallest bigger ring that holds it.
    Even depth = outer edge of a part; odd depth = a hole of its parent.
    """
    polys = sorted(polys, key=lambda p: p.area, reverse=True)
    depth = [0] * len(polys)
    parent = [-1] * len(polys)
    for i, p in enumerate(polys):
        for j in range(i - 1, -1, -1):          # walk back = smallest container first
            if _inside(p, polys[j]):
                parent[i], depth[i] = j, depth[j] + 1
                break
    holes: dict[int, list[Polygon]] = {i: [] for i in range(len(polys)) if depth[i] % 2 == 0}
    for i in range(len(polys)):
        if depth[i] % 2 == 1:
            holes[parent[i]].append(polys[i])
    shapes: list[Polygon] = []
    for i, hs in holes.items():
        shape = polys[i].difference(unary_union(hs)) if hs else polys[i]
        for g in getattr(shape, "geoms", [shape]):   # overlapping holes may split a part
            if isinstance(g, Polygon) and g.area >= MIN_RING_AREA_MM2:
                shapes.append(orient(g, 1.0))
    return shapes


def _reading_order(shapes: list[Polygon]) -> list[Polygon]:
    """Sort parts top-to-bottom (rows), then left-to-right inside a row, as seen in the file."""
    by_top = sorted(shapes, key=lambda s: (-s.bounds[3], s.bounds[0]))
    rows: list[list[Polygon]] = []
    row_floor = None
    for s in by_top:
        # a part starts a new row when its top is below the middle of the row's first part
        if row_floor is None or s.bounds[3] < row_floor:
            rows.append([])
            b = s.bounds
            row_floor = (b[1] + b[3]) / 2.0
        rows[-1].append(s)
    return [s for row in rows for s in sorted(row, key=lambda s: s.bounds[0])]


def _assign_marks(lines: list[_Piece], shapes: list[Polygon],
                  warnings: list[str]) -> dict[int, list[tuple[LineString, str]]]:
    """Give each open line to the smallest part that contains its midpoint."""
    marks: dict[int, list[tuple[LineString, str]]] = {}
    order = sorted(range(len(shapes)), key=lambda i: shapes[i].area)
    dropped = 0
    kept = {"engrave": 0, "score": 0}
    for ln in lines:
        line = LineString(ln.pts)
        if line.length <= 0:
            continue
        mid = line.interpolate(0.5, normalized=True)
        home = next((i for i in order if shapes[i].covers(mid)), None)
        if home is None:
            dropped += 1
            continue
        marks.setdefault(home, []).append((line, ln.layer))
        kept[ln.layer] += 1
    for layer, n in kept.items():
        if n:
            warnings.append(f"{n} open line(s) were kept as {layer} marks")
    if dropped:
        warnings.append(f"{dropped} open line(s) outside every part were dropped")
    return marks


def _mark_layer(marks: list[tuple[LineString, str]], name: str, warnings: list[str]) -> str:
    """One layer per part (Part.mark_layer): the layer most of its lines use."""
    score = sum(1 for _, layer in marks if layer == "score")
    if score and score != len(marks):
        warnings.append(f"{name} has both score and engrave lines; all were set to "
                        f"{'score' if score * 2 >= len(marks) else 'engrave'}")
    return "score" if score * 2 >= len(marks) and score else "engrave"


def _build_parts(pieces: list[_Piece], stem: str, source: str, warnings: list[str]) -> list[Part]:
    """Chains -> rings -> grouped polygons -> named, normalised Parts with marks."""
    joined = _join_chains(pieces)
    rings = [p for p in joined if p.closed]
    lines = [p for p in joined if not p.closed]
    shapes = _reading_order(_group_rings(_rings_to_polygons(rings)))
    marks = _assign_marks(lines, shapes, warnings)
    parts: list[Part] = []
    for n, shape in enumerate(shapes):
        dx, dy = -shape.bounds[0], -shape.bounds[1]
        name = f"{stem}-{n + 1}"
        my = marks.get(n, [])
        parts.append(Part(
            id=uuid.uuid4().hex[:8],
            name=name,
            shape=affinity.translate(shape, dx, dy),
            qty=1,
            marks=[affinity.translate(m, dx, dy) for m, _ in my],
            mark_layer=_mark_layer(my, name, warnings) if my else "engrave",
            source=source,
        ))
    return parts


__all__ = ["ImportError_", "ImportReport", "import_file"]
