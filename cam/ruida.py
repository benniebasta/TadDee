"""Laser output: one DXF per sheet for the Ruida controller software.

The laser software maps layer colours to power and speed:
CUT red (1), SCORE blue (5), ENGRAVE black/white (7), SHEET grey (8, only
when the sheet outline is asked for). Units mm, R2010, entity colour BYLAYER.
"""
from __future__ import annotations

import ezdxf
from shapely.geometry import LineString, MultiLineString, Polygon

from core.models import Part, SheetResult, placed_part

from .toolpaths import _clean_ring, _polygons

LAYER_COLOURS = {"CUT": 1, "SCORE": 5, "ENGRAVE": 7, "SHEET": 8}
_MARK_LAYER = {"score": "SCORE", "engrave": "ENGRAVE", "cut": "CUT"}
_BYLAYER = 256


def _kerf_rings(ring_pts, distance: float):
    """Offset a ring (as a hole-free polygon) by distance; if the offset
    vanishes (a hole smaller than the kerf) keep the original ring."""
    if abs(distance) < 1e-9:
        return [ring_pts]
    res = Polygon(ring_pts).buffer(distance, join_style="round")
    rings = [r for r in (_clean_ring(p.exterior.coords) for p in _polygons(res)) if r]
    return rings or [ring_pts]


def write_ruida_dxf(path: str, sheet: SheetResult, parts: dict[str, Part], *,
                    kerf_mm: float = 0.0, include_sheet_outline: bool = False,
                    info: dict[str, str] | None = None) -> int:
    """Write the sheet as DXF and return the number of entities written.
    With kerf_mm > 0 outlines move out by kerf/2 and holes move in by kerf/2,
    so the finished part comes out at its drawn size. Marks are never offset.

    `info` (job, sheet, revision, material, date...) is stored as DXF custom
    header properties only, never as entities: the laser software would treat
    a text entity as something to burn. The revision is also in the file name."""
    doc = ezdxf.new("R2010", setup=False)
    doc.header["$INSUNITS"] = 4        # millimetres
    doc.header["$MEASUREMENT"] = 1     # metric
    doc.units = 4
    for key, value in (info or {}).items():
        doc.header.custom_vars.append(str(key)[:64], str(value)[:250])
    for name, colour in LAYER_COLOURS.items():
        if name == "SHEET" and not include_sheet_outline:
            continue
        doc.layers.add(name, color=colour)
    msp = doc.modelspace()
    k = max(0.0, float(kerf_mm)) / 2.0
    count = 0

    def add(points, layer: str, closed: bool):
        nonlocal count
        msp.add_lwpolyline(points, close=closed, dxfattribs={"layer": layer, "color": _BYLAYER})
        count += 1

    if include_sheet_outline:
        w, h = sheet.width, sheet.height
        add([(0, 0), (w, 0), (w, h), (0, h)], "SHEET", True)

    for pl in sheet.placements:
        part = parts.get(pl.part_id)
        if part is None:
            continue
        shape, marks = placed_part(part, pl)
        if not shape.is_valid:
            shape = shape.buffer(0)
        for poly in _polygons(shape):
            ext = _clean_ring(poly.exterior.coords)
            if ext:
                for rp in _kerf_rings(ext, k):
                    add(rp, "CUT", True)
            for hole in poly.interiors:
                hp = _clean_ring(hole.coords)
                if hp:
                    for rp in _kerf_rings(hp, -k):
                        add(rp, "CUT", True)
        layer = _MARK_LAYER.get(part.mark_layer, "ENGRAVE")
        for m in marks:
            lines = list(m.geoms) if isinstance(m, MultiLineString) else [m]
            for ln in lines:
                if isinstance(ln, LineString) and not ln.is_empty and len(ln.coords) >= 2:
                    add([(float(c[0]), float(c[1])) for c in ln.coords], layer, False)

    doc.saveas(path)
    return count


__all__ = ["write_ruida_dxf", "LAYER_COLOURS"]
