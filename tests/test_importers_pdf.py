"""Tests for the PDF / AI importer (PLAN.md Part 2, item 2.10).

The fixture PDF is drawn here with pymupdf, so the expected sizes are exact.
"""
from __future__ import annotations

import io
import math

import pymupdf
import pytest
from PIL import Image

from core.importers import ImportError_, import_file

MM = 72.0 / 25.4          # points per mm


def pt(v_mm: float) -> float:
    return v_mm * MM


def sample_pdf(*, text: bool = False, blue_line: bool = True, extra_page: bool = False) -> bytes:
    """A4 page: 120 x 80 mm plate with a 30 mm round hole, a closed shape with a
    curved top edge (60 mm wide, 40 mm high at the middle), optional blue score line."""
    doc = pymupdf.open()
    page = doc.new_page(width=pt(210), height=pt(297))
    sh = page.new_shape()
    sh.draw_rect(pymupdf.Rect(pt(20), pt(20), pt(140), pt(100)))        # plate 120 x 80
    sh.draw_circle(pymupdf.Point(pt(50), pt(60)), pt(15))               # hole D30
    sh.finish(color=(0, 0, 0), fill=None, closePath=False)
    # curved part: bottom line 60 mm, sides 20 mm, top = cubic bezier peaking 20 mm above
    x0, x1, yb = pt(20), pt(80), pt(200)
    sh.draw_line(pymupdf.Point(x0, yb), pymupdf.Point(x1, yb))
    sh.draw_line(pymupdf.Point(x1, yb), pymupdf.Point(x1, yb - pt(20)))
    # control points 4/3 * 20 mm above the side tops -> the curve peaks 20 mm above them
    c = pt(20) * 4 / 3
    sh.draw_bezier(pymupdf.Point(x1, yb - pt(20)), pymupdf.Point(x1, yb - pt(20) - c),
                   pymupdf.Point(x0, yb - pt(20) - c), pymupdf.Point(x0, yb - pt(20)))
    sh.draw_line(pymupdf.Point(x0, yb - pt(20)), pymupdf.Point(x0, yb))
    sh.finish(color=(0, 0, 0), fill=None, closePath=True)
    if blue_line:
        sh.draw_line(pymupdf.Point(pt(90), pt(60)), pymupdf.Point(pt(130), pt(60)))
        sh.finish(color=(0, 0, 1), fill=None, closePath=False)
    sh.commit()
    if text:
        page.insert_text(pymupdf.Point(pt(100), pt(250)), "SALE 50%", fontsize=24)
    if extra_page:
        p2 = doc.new_page(width=pt(100), height=pt(100))
        s2 = p2.new_shape()
        s2.draw_rect(pymupdf.Rect(pt(10), pt(10), pt(60), pt(35)))      # 50 x 25
        s2.finish(color=(0, 0, 0), fill=None)
        s2.commit()
    return doc.tobytes()


def png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), (200, 30, 30)).save(buf, "PNG")
    return buf.getvalue()


def by_area(parts):
    return sorted(parts, key=lambda p: p.area, reverse=True)


def test_pdf_rect_with_hole_and_curve():
    rep = import_file(sample_pdf(), "plate.pdf")
    assert rep.units == "pt"
    assert len(rep.parts) == 2
    plate, curved = by_area(rep.parts)
    assert plate.width == pytest.approx(120, abs=0.05)
    assert plate.height == pytest.approx(80, abs=0.05)
    assert len(plate.shape.interiors) == 1
    hole = plate.shape.interiors[0]
    hx0, hy0, hx1, hy1 = hole.bounds
    assert hx1 - hx0 == pytest.approx(30, abs=0.05)
    assert hy1 - hy0 == pytest.approx(30, abs=0.05)
    # hole centre 30 mm from the left and 40 mm from the top (= 40 from the bottom, Y flipped)
    assert (hx0 + hx1) / 2 == pytest.approx(30, abs=0.05)
    assert (hy0 + hy1) / 2 == pytest.approx(40, abs=0.05)
    assert plate.area == pytest.approx(120 * 80 - math.pi * 15 ** 2, rel=2e-3)
    # curve: 60 wide, 20 mm side + 15 mm of bezier rise (3/4 of the control height) = 35 high
    assert curved.width == pytest.approx(60, abs=0.05)
    assert curved.height == pytest.approx(20 + 0.75 * 20 * 4 / 3, abs=0.05)
    assert len(curved.shape.exterior.coords) > 10          # the curve was flattened, not a chord


def test_pdf_y_is_flipped_and_reading_order():
    rep = import_file(sample_pdf(), "plate.pdf")
    # the plate is higher on the page, so it is part 1 in reading order
    assert rep.parts[0].name == "plate-1"
    assert rep.parts[0].width == pytest.approx(120, abs=0.05)
    assert rep.parts[1].name == "plate-2"
    # the curved edge is at the top after the Y flip: the widest row is near max y
    cur = rep.parts[1].shape
    top = max(y for _, y in cur.exterior.coords)
    near_top = [x for x, y in cur.exterior.coords if y > top - 0.5]
    assert max(near_top) - min(near_top) < 20                 # pointed top, flat bottom
    bottom = [x for x, y in cur.exterior.coords if y < 0.01]
    assert max(bottom) - min(bottom) == pytest.approx(60, abs=0.05)


def test_pdf_blue_stroke_is_score_mark():
    rep = import_file(sample_pdf(), "plate.pdf")
    plate = by_area(rep.parts)[0]
    assert len(plate.marks) == 1
    assert plate.mark_layer == "score"
    assert plate.marks[0].length == pytest.approx(40, abs=0.05)
    assert any("score" in w for w in rep.warnings)


def test_pdf_text_not_outlined_warns():
    rep = import_file(sample_pdf(text=True), "sign.pdf")
    assert any("text not converted to outlines" in w for w in rep.warnings)
    assert len(rep.parts) == 2
    rep2 = import_file(sample_pdf(), "sign.pdf")
    assert not any("text" in w for w in rep2.warnings)


def test_ai_pdf_compatible_reads_like_pdf():
    rep = import_file(sample_pdf(), "logo.ai")
    assert len(rep.parts) == 2
    assert by_area(rep.parts)[0].width == pytest.approx(120, abs=0.05)
    assert rep.parts[0].name == "logo-1"


def test_old_ai_without_pdf_content():
    with pytest.raises(ImportError_, match="PDF Compatible"):
        import_file(b"%!PS-Adobe-3.0\n%%Creator: Adobe Illustrator(R) 8.0\n", "old.ai")


def test_raster_only_pdf_is_a_clear_error():
    doc = pymupdf.open()
    page = doc.new_page(width=pt(100), height=pt(100))
    page.insert_image(pymupdf.Rect(10, 10, 200, 150), stream=png_bytes())
    with pytest.raises(ImportError_, match="only pictures"):
        import_file(doc.tobytes(), "scan.pdf")


def test_picture_next_to_vectors_warns():
    doc = pymupdf.open(stream=sample_pdf(blue_line=False), filetype="pdf")
    doc[0].insert_image(pymupdf.Rect(pt(150), pt(150), pt(200), pt(190)), stream=png_bytes())
    rep = import_file(doc.tobytes(), "mixed.pdf")
    assert len(rep.parts) == 2
    assert any("image(s) skipped" in w for w in rep.warnings)


def test_multi_page_pdf_stacks_pages():
    rep = import_file(sample_pdf(blue_line=False, extra_page=True), "two.pdf")
    assert len(rep.parts) == 3
    assert rep.parts[-1].width == pytest.approx(50, abs=0.05)
    assert rep.parts[-1].height == pytest.approx(25, abs=0.05)
    assert any("2 pages" in w for w in rep.warnings)


def test_filled_shape_without_stroke_is_closed():
    doc = pymupdf.open()
    page = doc.new_page(width=pt(100), height=pt(100))
    sh = page.new_shape()
    sh.draw_polyline([pymupdf.Point(pt(10), pt(10)), pymupdf.Point(pt(70), pt(10)), pymupdf.Point(pt(10), pt(50))])
    sh.finish(color=None, fill=(0, 0, 0), closePath=False)
    sh.commit()
    rep = import_file(doc.tobytes(), "tri.pdf")
    assert len(rep.parts) == 1
    assert rep.parts[0].area == pytest.approx(60 * 40 / 2, abs=0.5)


def test_damaged_pdf():
    with pytest.raises(ImportError_):
        import_file(b"%PDF-1.7\nthis is not really a pdf", "bad.pdf")


def test_upload_endpoint_accepts_pdf(tmp_path):
    from fastapi.testclient import TestClient
    from app.main import create_app

    with TestClient(create_app(str(tmp_path / "data"))) as c:
        mats = c.get("/api/materials").json()
        machines = c.get("/api/machines").json()
        job = c.post("/api/jobs", json={"name": "pdf", "material_id": mats[0]["id"],
                                         "machine_id": machines[0]["id"]}).json()
        r = c.post(f"/api/jobs/{job['id']}/files",
                   files=[("file", ("plate.pdf", sample_pdf(text=True), "application/pdf"))])
        assert r.status_code == 200, r.text
        body = r.json()
        assert len(body["parts"]) == 2
        assert any("text not converted to outlines" in w for w in body["warnings"])
