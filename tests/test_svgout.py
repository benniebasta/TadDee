"""Tests for core.svgout (PLAN.md 4.3)."""
from __future__ import annotations

import xml.etree.ElementTree as ET

from shapely.geometry import LineString, Polygon, box

from core.models import Part, Placement, SheetResult
from core.svgout import PALETTE, part_svg, sheet_svg

NS = "{http://www.w3.org/2000/svg}"


def plate() -> Part:
    shape = Polygon([(0, 0), (200, 0), (200, 100), (0, 100)],
                    [[(50, 25), (100, 25), (100, 75), (50, 75)]])
    return Part(id="p1", name="plate & co", shape=shape, marks=[LineString([(120, 50), (180, 50)])])


def disc() -> Part:
    from shapely.geometry import Point
    from shapely import affinity
    c = Point(30, 30).buffer(30)
    return Part(id="p2", name="disc", shape=affinity.translate(c, -c.bounds[0], -c.bounds[1]))


def sheet(with_offcut: bool = True) -> SheetResult:
    return SheetResult(
        sheet_id="s1", index=0, width=1000, height=500,
        placements=[Placement("p1", 0, 10, 10, 0), Placement("p1", 1, 400, 300, 90),
                    Placement("p2", 0, 60, 30, 0)],
        used_area=0, utilisation=0.3,
        offcut=box(620, 10, 990, 490) if with_offcut else None)


def test_part_svg_is_parseable_with_one_shape_and_marks():
    root = ET.fromstring(part_svg(plate(), size_px=64))
    assert root.tag == NS + "svg"
    assert root.get("width") == "64"
    paths = root.findall(f".//{NS}path")
    assert len(paths) == 2                                  # shape + 1 mark
    assert paths[0].get("fill-rule") == "evenodd"
    assert paths[0].get("d").count("M") == 2                # exterior + hole
    assert "matrix(1 0 0 -1" in ET.tostring(root, encoding="unicode")


def test_sheet_svg_paths_and_attributes():
    parts = {"p1": plate(), "p2": disc()}
    text = sheet_svg(sheet(), parts, margin=10)
    root = ET.fromstring(text)
    assert root.get("viewBox") == "0 0 1000 500"
    part_paths = [p for p in root.iter(NS + "path") if p.get("data-part-id")]
    assert len(part_paths) == 3
    assert [(p.get("data-part-id"), p.get("data-copy")) for p in part_paths] == \
        [("p1", "0"), ("p1", "1"), ("p2", "0")]
    assert all(p.get("fill-rule") == "evenodd" for p in part_paths)
    assert part_paths[0].get("fill") == PALETTE[0] and part_paths[2].get("fill") == PALETTE[1]
    mark_paths = [p for p in root.iter(NS + "path") if p.get("fill") == "none"]
    assert len(mark_paths) == 2                             # one mark per plate copy
    dashed = [r for r in root.iter(NS + "rect") if r.get("stroke-dasharray")]
    assert len(dashed) == 1 and dashed[0].get("x") == "10"
    assert root.find(f".//{NS}pattern") is not None         # offcut hatch
    texts = [t.text for t in root.iter(NS + "text")]
    assert "370 x 480 mm" in texts                          # offcut size
    assert "plate & co" in texts and texts.count("disc") == 1   # labels, escaped safely


def test_labels_are_outside_the_flipped_group():
    root = ET.fromstring(sheet_svg(sheet(), {"p1": plate(), "p2": disc()}))
    group = root.find(NS + "g")
    assert group.get("transform") == "matrix(1 0 0 -1 0 500)"
    assert group.find(f".//{NS}text") is None
    assert root.findall(NS + "text")


def test_sheet_svg_options_off():
    root = ET.fromstring(sheet_svg(sheet(), {"p1": plate(), "p2": disc()},
                                   show_offcut=False, labels=False))
    assert not list(root.iter(NS + "text"))
    assert root.find(f".//{NS}pattern") is None
    assert not [r for r in root.iter(NS + "rect") if r.get("stroke-dasharray")]   # margin 0 = no line


def test_sheet_svg_without_offcut_and_unknown_part():
    s = sheet(with_offcut=False)
    s.placements.append(Placement("missing", 0, 0, 0, 0))
    root = ET.fromstring(sheet_svg(s, {"p1": plate(), "p2": disc()}))
    assert len([p for p in root.iter(NS + "path") if p.get("data-part-id")]) == 3
