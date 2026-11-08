"""Tests for core.importers (PLAN.md 4.2 and section 6)."""
from __future__ import annotations

import io
import math
from pathlib import Path

import ezdxf
import pytest

from core.importers import ImportError_, ImportReport, import_file

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


def svg(body: str, w: str = "200mm", h: str = "200mm", vb: str = "0 0 200 200") -> bytes:
    """Wrap SVG body in a root element sized in mm (1 user unit = 1 mm by default)."""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
            f'viewBox="{vb}">{body}</svg>').encode()


def dxf_bytes(build, insunits: int = 4) -> bytes:
    """Make a DXF in memory: build(msp, doc) adds entities."""
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = insunits
    build(doc.modelspace(), doc)
    s = io.StringIO()
    doc.write(s)
    return s.getvalue().encode("utf-8")


def circle_d(cx, cy, r):
    return f"M{cx - r} {cy} A{r} {r} 0 1 0 {cx + r} {cy} A{r} {r} 0 1 0 {cx - r} {cy} Z"


def by_size(parts):
    return sorted(parts, key=lambda p: p.area, reverse=True)


# --------------------------------------------------------------------- SVG

def test_ring_hole_and_island_give_two_parts():
    body = (f'<path fill-rule="evenodd" d="{circle_d(100, 100, 60)} {circle_d(100, 100, 40)}"/>'
            f'<circle cx="100" cy="100" r="20"/>')
    rep = import_file(svg(body), "ring.svg")
    assert isinstance(rep, ImportReport)
    assert len(rep.parts) == 2
    ring, island = by_size(rep.parts)
    assert len(ring.shape.interiors) == 1
    assert len(island.shape.interiors) == 0
    assert ring.area == pytest.approx(math.pi * (60 ** 2 - 40 ** 2), rel=0.01)
    assert island.width == pytest.approx(40, abs=0.1)
    for p in rep.parts:
        assert p.shape.is_valid
        assert p.shape.bounds[0] == pytest.approx(0, abs=1e-9)
        assert p.shape.bounds[1] == pytest.approx(0, abs=1e-9)
        assert len(p.id) == 8 and p.qty == 1 and p.source == "ring.svg"


def test_bezier_is_flattened_within_tolerance():
    # four cubic quarter-circles approximate a circle of r = 50 very closely
    k = 0.5523 * 50
    d = (f"M150 100 C150 {100 + k} {100 + k} 150 100 150 C{100 - k} 150 50 {100 + k} 50 100 "
         f"C50 {100 - k} {100 - k} 50 100 50 C{100 + k} 50 150 {100 - k} 150 100 Z")
    rep = import_file(svg(f'<path d="{d}"/>'), "blob.svg", tolerance_mm=0.05)
    (p,) = rep.parts
    assert len(p.shape.exterior.coords) > 40          # really curved, not 4 corners
    assert p.area == pytest.approx(math.pi * 50 ** 2, rel=0.005)
    coarse = import_file(svg(f'<path d="{d}"/>'), "blob.svg", tolerance_mm=1.0).parts[0]
    assert len(coarse.shape.exterior.coords) < len(p.shape.exterior.coords)


def test_transforms_are_applied():
    body = '<g transform="translate(20 30) scale(2)"><rect x="0" y="0" width="30" height="10"/></g>'
    (p,) = import_file(svg(body), "t.svg").parts
    assert p.width == pytest.approx(60, abs=1e-6)
    assert p.height == pytest.approx(20, abs=1e-6)
    rot = '<rect x="50" y="50" width="40" height="10" transform="rotate(90 50 50)"/>'
    (q,) = import_file(svg(rot), "r.svg").parts
    assert q.width == pytest.approx(10, abs=1e-6) and q.height == pytest.approx(40, abs=1e-6)


def test_mm_units_and_viewbox_scaling():
    # 100 mm wide page, viewBox 0..1000 -> 1 user unit = 0.1 mm
    rep = import_file(svg('<rect width="500" height="250"/>', w="100mm", h="100mm",
                          vb="0 0 1000 1000"), "u.svg")
    assert rep.units == "mm"
    assert rep.parts[0].width == pytest.approx(50, abs=1e-6)
    assert rep.parts[0].height == pytest.approx(25, abs=1e-6)


def test_px_units_are_96_ppi():
    data = b'<svg xmlns="http://www.w3.org/2000/svg"><rect width="96" height="48"/></svg>'
    rep = import_file(data, "px.svg")
    assert rep.units == "px"
    assert rep.parts[0].width == pytest.approx(25.4, abs=1e-6)
    assert rep.parts[0].height == pytest.approx(12.7, abs=1e-6)


def test_inch_units():
    rep = import_file(svg('<rect width="1" height="1"/>', w="2in", h="2in", vb="0 0 2 2"), "i.svg")
    assert rep.units == "inch"
    assert rep.parts[0].width == pytest.approx(25.4, abs=1e-6)


def test_y_is_flipped_and_names_follow_reading_order():
    # in SVG, y grows down: the rect at y=10 is the TOP one
    body = ('<rect x="10" y="120" width="40" height="40"/>'      # bottom-left
            '<rect x="110" y="10" width="30" height="30"/>'      # top-right
            '<rect x="10" y="10" width="20" height="20"/>'       # top-left
            '<line x1="15" y1="13" x2="25" y2="13" stroke="#000"/>')   # near the top of top-left rect
    rep = import_file(svg(body), "order.svg")
    names = {p.name: p for p in rep.parts}
    assert [p.name for p in rep.parts] == ["order-1", "order-2", "order-3"]
    assert names["order-1"].width == pytest.approx(20)      # top-left
    assert names["order-2"].width == pytest.approx(30)      # top-right
    assert names["order-3"].width == pytest.approx(40)      # bottom
    (mark,) = names["order-1"].marks
    # 3 mm below the top edge in SVG = y 17 in the part's Y-up frame (height 20)
    assert mark.coords[0][1] == pytest.approx(17, abs=1e-6)


def test_open_line_becomes_mark_blue_is_score():
    body = ('<rect x="0" y="0" width="100" height="50"/>'
            '<line x1="10" y1="25" x2="90" y2="25" stroke="#0000ff"/>'
            '<rect x="0" y="100" width="100" height="50"/>'
            '<polyline points="10,120 50,130 90,120" fill="none" stroke="black"/>'
            '<line x1="150" y1="150" x2="190" y2="190" stroke="black"/>')   # outside every part
    rep = import_file(svg(body), "marks.svg")
    top, bottom = rep.parts
    assert len(top.marks) == 1 and top.mark_layer == "score"
    assert len(bottom.marks) == 1 and bottom.mark_layer == "engrave"
    assert top.marks[0].length == pytest.approx(80, abs=1e-6)
    assert any("dropped" in w for w in rep.warnings)
    assert any("score marks" in w for w in rep.warnings)


def test_text_and_image_are_skipped_with_warning():
    body = ('<rect width="50" height="50"/><text x="10" y="80">Hello</text>'
            '<image x="0" y="100" width="10" height="10" href="data:image/png;base64,AAAA"/>')
    rep = import_file(svg(body), "text.svg")
    assert len(rep.parts) == 1
    assert any("text must be converted to outlines" in w for w in rep.warnings)
    assert any("image" in w for w in rep.warnings)


def test_invisible_shapes_are_skipped():
    body = ('<rect width="50" height="50"/>'
            '<rect x="100" width="50" height="50" fill="none"/>'
            '<rect x="100" y="100" width="50" height="50" style="display:none"/>')
    assert len(import_file(svg(body), "inv.svg").parts) == 1


def test_tiny_rings_are_dropped():
    body = '<rect width="50" height="50"/><rect x="100" width="0.5" height="0.5"/>'
    assert len(import_file(svg(body), "dust.svg").parts) == 1


def test_corrupt_svg_raises_clean_error():
    with pytest.raises(ImportError_) as e:
        import_file(b"<svg xmlns='http://www.w3.org/2000/svg'><path d='M0 0 L", "bad.svg")
    assert "Traceback" not in str(e.value)
    with pytest.raises(ImportError_):
        import_file(b"\x00\x01\x02 not xml at all", "bad.svg")


def test_no_closed_shapes():
    with pytest.raises(ImportError_, match="No closed shapes found"):
        import_file(svg('<line x1="0" y1="0" x2="10" y2="10" stroke="black"/>'), "lines.svg")


def test_wrong_extension_and_empty():
    with pytest.raises(ImportError_, match="Only SVG or DXF files"):
        import_file(b"hello", "drawing.pdf")
    with pytest.raises(ImportError_):
        import_file(b"", "empty.svg")


# --------------------------------------------------------------------- DXF

def test_dxf_lwpolyline_with_bulges():
    def build(msp, doc):
        msp.add_lwpolyline([(30, 0, 0), (130, 0, 1), (130, 60, 0), (30, 60, 1)],
                           format="xyb", close=True)
    (p,) = import_file(dxf_bytes(build), "obround.dxf").parts
    assert p.width == pytest.approx(160, abs=0.05)
    assert p.height == pytest.approx(60, abs=0.1)
    assert p.area == pytest.approx(100 * 60 + math.pi * 30 ** 2, rel=0.005)


def test_dxf_circle_and_line_chain_rectangle_with_hole():
    def build(msp, doc):
        pts = [(0, 0), (150, 0), (150, 100), (0, 100)]
        # LINEs in mixed directions and order, with a 0.01 mm gap: still one closed ring
        msp.add_line(pts[1], pts[0])
        msp.add_line(pts[2], pts[3])
        msp.add_line((150.0, 0.01), pts[2])
        msp.add_line(pts[3], pts[0])
        msp.add_circle((75, 50), 20)
        msp.add_circle((300, 50), 30)
    rep = import_file(dxf_bytes(build), "rect.dxf")
    rect, disc = by_size(rep.parts)
    assert rep.units == "mm"
    assert (rect.width, rect.height) == (pytest.approx(150), pytest.approx(100))
    assert len(rect.shape.interiors) == 1
    assert disc.width == pytest.approx(60, abs=0.01)
    assert disc.area == pytest.approx(math.pi * 900, rel=0.005)


def test_dxf_arc_and_line_slot():
    def build(msp, doc):
        msp.add_line((0, 0), (80, 0))
        msp.add_arc((80, 20), 20, -90, 90)
        msp.add_line((80, 40), (0, 40))
        msp.add_arc((0, 20), 20, 90, 270)
    (p,) = import_file(dxf_bytes(build), "slot.dxf").parts
    assert p.width == pytest.approx(120, abs=0.05)
    assert p.area == pytest.approx(80 * 40 + math.pi * 400, rel=0.005)


def test_dxf_inch_units():
    def build(msp, doc):
        msp.add_lwpolyline([(0, 0), (2, 0), (2, 1), (0, 1)], close=True)
    rep = import_file(dxf_bytes(build, insunits=1), "inch.dxf")
    assert rep.units == "inch"
    assert rep.parts[0].width == pytest.approx(50.8)
    assert rep.parts[0].height == pytest.approx(25.4)


def test_dxf_unitless_uses_default_units():
    def build(msp, doc):
        msp.add_lwpolyline([(0, 0), (2, 0), (2, 1), (0, 1)], close=True)
    rep = import_file(dxf_bytes(build, insunits=0), "x.dxf", default_units="inch")
    assert rep.units == "inch" and rep.parts[0].width == pytest.approx(50.8)
    rep = import_file(dxf_bytes(build, insunits=0), "x.dxf")
    assert rep.units == "mm" and rep.parts[0].width == pytest.approx(2)


def test_dxf_insert_is_exploded_and_open_line_is_mark():
    def build(msp, doc):
        blk = doc.blocks.new("SQ")
        blk.add_lwpolyline([(0, 0), (40, 0), (40, 40), (0, 40)], close=True)
        msp.add_blockref("SQ", (0, 0))
        msp.add_blockref("SQ", (100, 0), dxfattribs={"xscale": 2, "yscale": 2})
        msp.add_line((5, 20), (35, 20))                        # engrave line in the first square
        msp.add_text("hello", dxfattribs={"insert": (0, 100)})
    rep = import_file(dxf_bytes(build), "blocks.dxf")
    big, small = by_size(rep.parts)
    assert big.width == pytest.approx(80) and small.width == pytest.approx(40)
    assert len(small.marks) == 1 and small.mark_layer == "engrave"
    assert any("text" in w for w in rep.warnings)


def test_corrupt_dxf_raises_clean_error():
    with pytest.raises(ImportError_) as e:
        import_file(b"0\nSECTION\n2\nENTITIES\n0\nLINE\n10\nnot-a-number\n", "bad.dxf")
    assert "Traceback" not in str(e.value)
    with pytest.raises(ImportError_):
        import_file(b"this is not a dxf file", "bad.dxf")


# ----------------------------------------------------------------- samples

@pytest.mark.parametrize("name,count", [("parts.svg", 6), ("letters.svg", 4), ("parts.dxf", 4)])
def test_sample_files_import(name, count):
    path = SAMPLES / name
    if not path.exists():
        pytest.skip("run samples/make_samples.py first")
    rep = import_file(path.read_bytes(), name)
    assert len(rep.parts) == count
    assert all(p.shape.is_valid and p.area > 100 for p in rep.parts)
