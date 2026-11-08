"""SVG previews of parts and nested sheets (PLAN.md section 4.3).

Pure strings, no external CSS. Geometry is in mm with Y up; SVG screens are
Y down, so shapes are drawn inside a group with transform
matrix(1 0 0 -1 0 H), which flips them. Text is drawn outside that group
(with its y flipped by hand) so labels read the right way round.
"""
from __future__ import annotations

from xml.sax.saxutils import escape, quoteattr

from shapely.geometry import LineString, MultiPolygon, Polygon

from core.models import Part, SheetResult, placed_part

# 8 soft pastel fills, picked by part index (same part = same colour on every sheet)
PALETTE = ["#a8d5ff", "#ffd6a5", "#caffbf", "#ffc6ff", "#fdffb6", "#bdb2ff", "#9bf6ff", "#ffadad"]
PART_STROKE = "#3b4252"
MARK_STROKE = "#1b1f24"
SHEET_FILL = "#f4f5f7"
SHEET_STROKE = "#8c959f"
OFFCUT_FILL = "#dff5e1"
OFFCUT_HATCH = "#5cb85c"


def _f(v: float) -> str:
    """Number for SVG: 3 decimals, trailing zeros stripped."""
    s = f"{float(v):.3f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _ring_d(coords) -> str:
    """Path data for one closed ring: M x y L ... Z."""
    pts = list(coords)
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]
    if not pts:
        return ""
    head = f"M{_f(pts[0][0])} {_f(pts[0][1])}"
    return head + "".join(f"L{_f(x)} {_f(y)}" for x, y in pts[1:]) + "Z"


def polygon_d(poly) -> str:
    """Path data for a Polygon (or MultiPolygon): exterior plus every hole."""
    polys = poly.geoms if isinstance(poly, MultiPolygon) else [poly]
    out = []
    for p in polys:
        if isinstance(p, Polygon) and not p.is_empty:
            out.append(_ring_d(p.exterior.coords))
            out.extend(_ring_d(r.coords) for r in p.interiors)
    return "".join(out)


def _line_d(line: LineString) -> str:
    """Path data for an open line: M x y L ..."""
    pts = list(line.coords)
    if len(pts) < 2:
        return ""
    return f"M{_f(pts[0][0])} {_f(pts[0][1])}" + "".join(f"L{_f(x)} {_f(y)}" for x, y in pts[1:])


def part_svg(part: Part, size_px: int = 80) -> str:
    """Small thumbnail of one part, fitted into a size_px square, Y flipped for screen."""
    minx, miny, maxx, maxy = part.shape.bounds
    w, h = max(maxx - minx, 1e-6), max(maxy - miny, 1e-6)
    pad = max(w, h) * 0.04
    view = f"{_f(minx - pad)} {_f(-maxy - pad)} {_f(w + 2 * pad)} {_f(h + 2 * pad)}"
    marks = "".join(
        f'<path d="{_line_d(m)}" fill="none" stroke="{MARK_STROKE}" stroke-width="1" '
        f'vector-effect="non-scaling-stroke"/>' for m in part.marks)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{int(size_px)}" height="{int(size_px)}" '
        f'viewBox="{view}" preserveAspectRatio="xMidYMid meet">'
        f'<g transform="matrix(1 0 0 -1 0 0)">'
        f'<path d="{polygon_d(part.shape)}" fill="{PALETTE[0]}" fill-rule="evenodd" '
        f'stroke="{PART_STROKE}" stroke-width="1" vector-effect="non-scaling-stroke"/>'
        f'{marks}</g></svg>'
    )


def _hatch_defs(pid: str) -> str:
    """A diagonal hatch pattern for the offcut, in mm."""
    return (
        f'<defs><pattern id="{pid}" patternUnits="userSpaceOnUse" width="20" height="20" '
        f'patternTransform="rotate(45)"><rect width="20" height="20" fill="{OFFCUT_FILL}"/>'
        f'<line x1="0" y1="0" x2="0" y2="20" stroke="{OFFCUT_HATCH}" stroke-width="2"/>'
        f'</pattern></defs>'
    )


def _sheet_frame(sheet: SheetResult, margin: float) -> str:
    """Sheet background and the dashed margin line (inside the flipped group)."""
    W, H = sheet.width, sheet.height
    out = [f'<rect x="0" y="0" width="{_f(W)}" height="{_f(H)}" fill="{SHEET_FILL}" '
           f'stroke="{SHEET_STROKE}" stroke-width="2" vector-effect="non-scaling-stroke"/>']
    if margin > 0 and W > 2 * margin and H > 2 * margin:
        out.append(f'<rect x="{_f(margin)}" y="{_f(margin)}" width="{_f(W - 2 * margin)}" '
                   f'height="{_f(H - 2 * margin)}" fill="none" stroke="{SHEET_STROKE}" '
                   f'stroke-width="1" stroke-dasharray="6 4" vector-effect="non-scaling-stroke"/>')
    return "".join(out)


def _label(text: str, x: float, y_screen: float, size: float) -> str:
    """One text label in screen coordinates (outside the flipped group)."""
    return (f'<text x="{_f(x)}" y="{_f(y_screen)}" font-size="{_f(size)}" '
            f'font-family="system-ui, sans-serif" fill="#1b1f24" text-anchor="middle" '
            f'dominant-baseline="middle">{escape(text)}</text>')


def sheet_svg(sheet: SheetResult, parts: dict[str, Part], *, show_offcut: bool = True,
              labels: bool = True, margin: float = 0.0) -> str:
    """Full preview of one nested sheet: sheet, margin, parts, marks, offcut, labels.

    margin is not stored on SheetResult, so the caller passes it to draw the dashed line.
    """
    W, H = sheet.width, sheet.height
    order = {pid: i for i, pid in enumerate(parts)}
    hatch_id = f"offcut-hatch-{sheet.index}"
    body: list[str] = [_sheet_frame(sheet, margin)]
    texts: list[str] = []

    has_offcut = show_offcut and sheet.offcut is not None and not sheet.offcut.is_empty
    if has_offcut:
        body.append(f'<path d="{polygon_d(sheet.offcut)}" fill="url(#{hatch_id})" fill-rule="evenodd" '
                    f'stroke="{OFFCUT_HATCH}" stroke-width="1" vector-effect="non-scaling-stroke"/>')
        texts.append(_offcut_text(sheet.offcut, H))

    for pl in sheet.placements:
        part = parts.get(pl.part_id)
        if part is None:
            continue
        shape, marks = placed_part(part, pl)
        colour = PALETTE[order.get(pl.part_id, 0) % len(PALETTE)]
        body.append(f'<path d="{polygon_d(shape)}" fill="{colour}" fill-rule="evenodd" '
                    f'stroke="{PART_STROKE}" stroke-width="1" vector-effect="non-scaling-stroke" '
                    f'data-part-id={quoteattr(pl.part_id)} data-copy="{int(pl.copy)}"/>')
        body.extend(f'<path d="{_line_d(m)}" fill="none" stroke="{MARK_STROKE}" stroke-width="0.8" '
                    f'vector-effect="non-scaling-stroke"/>' for m in marks)
        if labels:
            texts.append(_part_label(part.name, shape, H))

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {_f(W)} {_f(H)}" width="100%" '
        f'preserveAspectRatio="xMidYMid meet" data-sheet-index="{int(sheet.index)}">'
        + (_hatch_defs(hatch_id) if has_offcut else "")
        + f'<g transform="matrix(1 0 0 -1 0 {_f(H)})">' + "".join(body) + "</g>"
        + "".join(texts) + "</svg>"
    )


def _part_label(name: str, shape: Polygon, H: float) -> str:
    """Part name at a point inside the shape, sized to the part."""
    p = shape.representative_point()
    minx, miny, maxx, maxy = shape.bounds
    size = min(max(min(maxx - minx, maxy - miny) * 0.2, 8.0), 40.0)
    return _label(name, p.x, H - p.y, size)


def _offcut_text(offcut: Polygon, H: float) -> str:
    """'W x H mm' written in the middle of the offcut."""
    minx, miny, maxx, maxy = offcut.bounds
    w, h = maxx - minx, maxy - miny
    size = min(max(min(w, h) * 0.08, 12.0), 60.0)
    return _label(f"{w:.0f} x {h:.0f} mm", (minx + maxx) / 2, H - (miny + maxy) / 2, size)


__all__ = ["part_svg", "sheet_svg", "polygon_d", "PALETTE"]
