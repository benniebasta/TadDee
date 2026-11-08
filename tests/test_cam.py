"""Tests for cam/: toolpaths, NC Studio writer, Ruida DXF. Parts and sheets
are built by hand so these tests do not depend on the importer or nester."""
import math
import re

import ezdxf
import pytest
from shapely.geometry import LinearRing, LineString, Point, Polygon

from cam import CamSettings, CamTool, ToolPath, sheet_toolpaths, write_ncstudio, write_ruida_dxf
from core.models import Part, Placement, SheetResult, placed_part


def rect_with_hole(pid="p1", w=100.0, h=50.0, hole_d=20.0, marks=None, layer="engrave"):
    outer = [(0, 0), (w, 0), (w, h), (0, h)]
    hole = list(Point(w / 2, h / 2).buffer(hole_d / 2).exterior.coords)
    return Part(pid, f"part-{pid}", Polygon(outer, [hole]), marks=marks or [], mark_layer=layer)


def sheet_of(placements, w=600.0, h=400.0):
    return SheetResult("s1", 0, w, h, placements, used_area=0.0, utilisation=0.0)


CUT = CamTool("t6", "6 mm flat", 6.0, pass_depth_mm=3.0)
VBIT = CamTool("v60", "60 deg V-bit", 6.0, kind="vbit", angle_deg=60, pass_depth_mm=1.0)


def ring2d(tp: ToolPath):
    return [(x, y) for x, y, _ in tp.points]


def max_dist_to_boundary(tp, geom):
    return max(geom.distance(Point(p)) for p in ring2d(tp))


# ------------------------------------------------------------ geometry

def test_outside_offset_by_radius_and_hole_inside():
    part = rect_with_hole()
    pl = Placement("p1", 0, 20, 30, 0)
    s = CamSettings(thickness_mm=6, tabs=False)
    paths, warns = sheet_toolpaths(sheet_of([pl]), {"p1": part}, CUT, None, s)
    assert warns == []
    shape, _ = placed_part(part, pl)
    ext = LinearRing(shape.exterior.coords)
    hole = LinearRing(shape.interiors[0].coords)
    outs = [p for p in paths if p.kind == "outside"]
    ins = [p for p in paths if p.kind == "inside"]
    assert outs and ins
    for tp in outs:
        line = LineString(ring2d(tp))
        assert line.hausdorff_distance(ext) == pytest.approx(3.0, abs=0.05)
        # every point sits at r from the part and outside it
        for pt in ring2d(tp):
            assert ext.distance(Point(pt)) == pytest.approx(3.0, abs=0.05)
            assert not shape.contains(Point(pt))
    for tp in ins:
        hp = Polygon(hole)
        for pt in ring2d(tp):
            assert hp.contains(Point(pt))
            assert hole.distance(Point(pt)) == pytest.approx(3.0, abs=0.05)
    for tp in paths:
        assert tp.points[0] == tp.points[-1]   # closed


def test_rotated_placement_uses_place():
    part = rect_with_hole()
    pl = Placement("p1", 0, 200, 20, 90)
    paths, _ = sheet_toolpaths(sheet_of([pl]), {"p1": part}, CUT, None, CamSettings(thickness_mm=3, tabs=False))
    shape, _ = placed_part(part, pl)
    out = [p for p in paths if p.kind == "outside"][0]
    assert LineString(ring2d(out)).hausdorff_distance(shape.exterior) == pytest.approx(3.0, abs=0.05)


def test_tiny_hole_skipped_with_warning():
    part = rect_with_hole(hole_d=5.0)   # smaller than the 6 mm bit
    paths, warns = sheet_toolpaths(sheet_of([Placement("p1", 0, 10, 10, 0)]), {"p1": part},
                                   CUT, None, CamSettings(thickness_mm=3, tabs=False))
    assert not [p for p in paths if p.kind == "inside"]
    assert any("smaller than the 6 mm bit" in w and "part-p1" in w for w in warns)
    assert [p for p in paths if p.kind == "outside"]


def test_dumbbell_hole_splits_into_two_rings():
    # a hole made of two 20 mm squares joined by a 4 mm neck: the 6 mm bit
    # cannot pass the neck, so the inward offset is two separate rings
    hole = Polygon([(10, 10), (30, 10), (30, 18), (40, 18), (40, 10), (60, 10), (60, 30),
                    (40, 30), (40, 22), (30, 22), (30, 30), (10, 30)])
    part = Part("d", "dumb", Polygon([(0, 0), (70, 0), (70, 40), (0, 40)], [hole.exterior.coords]))
    paths, warns = sheet_toolpaths(sheet_of([Placement("d", 0, 0, 0, 0)]), {"d": part},
                                   CUT, None, CamSettings(thickness_mm=2.5, tabs=False))
    ins = [p for p in paths if p.kind == "inside"]
    assert len({ins_p.points[0][:2] for ins_p in ins}) == 2


def test_degenerate_geometry_does_not_crash():
    flat = Part("z", "zero", Polygon([(0, 0), (10, 0), (20, 0)]))
    paths, warns = sheet_toolpaths(sheet_of([Placement("z", 0, 0, 0, 0), Placement("missing", 0, 0, 0, 0)]),
                                   {"z": flat}, CUT, None, CamSettings(thickness_mm=3))
    assert isinstance(paths, list) and warns


@pytest.mark.parametrize("climb", [True, False])
def test_direction_follows_climb_flag(climb):
    part = rect_with_hole()
    paths, _ = sheet_toolpaths(sheet_of([Placement("p1", 0, 10, 10, 0)]), {"p1": part},
                               CUT, None, CamSettings(thickness_mm=3, tabs=False, climb=climb))
    for tp in paths:
        ccw = LinearRing(ring2d(tp)).is_ccw
        if tp.kind == "outside":
            assert ccw is (not climb)       # climb: outside clockwise
        else:
            assert ccw is climb             # climb: inside counter-clockwise


# ------------------------------------------------------------ depths and tabs

def test_levels_and_last_depth():
    part = rect_with_hole()
    s = CamSettings(thickness_mm=6, cut_through_mm=0.3, tabs=False)
    paths, _ = sheet_toolpaths(sheet_of([Placement("p1", 0, 10, 10, 0)]), {"p1": part}, CUT, None, s)
    outs = [p for p in paths if p.kind == "outside"]
    zs = [p.points[0][2] for p in outs]
    assert len(outs) == 3                          # ceil(6.3 / 3)
    assert zs == pytest.approx([-2.1, -4.2, -6.3])
    assert min(z for p in paths for _, _, z in p.points) == pytest.approx(-6.3)


def test_tabs_z_count_and_length():
    part = rect_with_hole()
    s = CamSettings(thickness_mm=6, cut_through_mm=0.3, tab_width_mm=6, tab_height_mm=1.5)
    paths, warns = sheet_toolpaths(sheet_of([Placement("p1", 0, 10, 10, 0)]), {"p1": part}, CUT, None, s)
    outs = [p for p in paths if p.kind == "outside"]
    # tabs only on the last level
    for tp in outs[:-1]:
        assert len({z for _, _, z in tp.points}) == 1
    last = outs[-1]
    tab_z = -6 + 1.5
    zs = [z for _, _, z in last.points]
    assert set(round(z, 6) for z in zs) == {-6.3, tab_z}
    # count the tabs: runs of points at tab Z
    runs, inside = [], False
    for x, y, z in last.points:
        if abs(z - tab_z) < 1e-9 and not inside:
            runs.append([(x, y)])
            inside = True
        elif abs(z - tab_z) < 1e-9:
            runs[-1].append((x, y))
        else:
            inside = False
    assert len(runs) == 2                          # perimeter ~319 mm -> clamp to 2
    for run in runs:
        length = sum(math.dist(a, b) for a, b in zip(run, run[1:]))
        assert length == pytest.approx(6 + 6, abs=1e-6)   # tab width + bit diameter
    assert last.points[0] == last.points[-1]
    assert not any("Tabs are" in w for w in warns)


def test_tabs_on_every_level_below_tab_top():
    part = rect_with_hole()
    s = CamSettings(thickness_mm=3, cut_through_mm=0.3)          # levels -1.65, -3.3, tab top -1.5
    paths, warns = sheet_toolpaths(sheet_of([Placement("p1", 0, 10, 10, 0)]), {"p1": part}, CUT, None, s)
    outs = [p for p in paths if p.kind == "outside"]
    assert len(outs) == 2
    for tp in outs:                                  # both levels go below -1.5, so both keep the tab
        assert max(z for _, _, z in tp.points) == pytest.approx(-1.5)
    assert not any("Tabs are" in w for w in warns)


def test_tab_count_grows_with_perimeter_and_min_part_size():
    big = Part("b", "big", Polygon([(0, 0), (1000, 0), (1000, 600), (0, 600)]))
    small = Part("s", "small", Polygon([(0, 0), (40, 0), (40, 40), (0, 40)]))
    s = CamSettings(thickness_mm=6, tab_min_part_mm=50)
    paths, _ = sheet_toolpaths(sheet_of([Placement("b", 0, 10, 10, 0), Placement("s", 0, 1100, 10, 0)], 2440, 1220),
                               {"b": big, "s": small}, CUT, None, s)
    tab_z = -4.5

    def n_tabs(tp):
        zs = [abs(z - tab_z) < 1e-9 for _, _, z in tp.points]
        return sum(1 for a, b in zip(zs, zs[1:]) if b and not a)
    last_big = [p for p in paths if p.part_id == "b"][-1]
    last_small = [p for p in paths if p.part_id == "s"][-1]
    assert n_tabs(last_big) == 8                   # 3237 mm / 400 -> 8
    assert n_tabs(last_small) == 0                 # 40 mm part < tab_min_part_mm


def test_marks_depth_tool_and_open():
    m = LineString([(20, 10), (80, 10), (80, 40)])
    part = rect_with_hole(marks=[m])
    s = CamSettings(thickness_mm=3, engrave_depth_mm=0.4)
    paths, _ = sheet_toolpaths(sheet_of([Placement("p1", 0, 10, 10, 0)]), {"p1": part}, CUT, VBIT, s)
    marks = [p for p in paths if p.kind == "mark"]
    assert len(marks) == 1
    tp = marks[0]
    assert tp.tool_id == "v60"
    assert {z for _, _, z in tp.points} == {-0.4}
    assert tp.points[0] != tp.points[-1]           # open line
    assert LineString(ring2d(tp)).equals(placed_part(part, Placement("p1", 0, 10, 10, 0))[1][0])
    # without a mark tool the cut tool engraves
    paths2, _ = sheet_toolpaths(sheet_of([Placement("p1", 0, 10, 10, 0)]), {"p1": part}, CUT, None, s)
    assert [p.tool_id for p in paths2 if p.kind == "mark"] == ["t6"]


def test_order_marks_holes_outside_and_levels_consecutive():
    parts = {"a": rect_with_hole("a", marks=[LineString([(5, 5), (30, 5)])]),
             "b": rect_with_hole("b", marks=[LineString([(5, 45), (30, 45)])])}
    pls = [Placement("b", 0, 300, 200, 0), Placement("a", 0, 10, 10, 0)]
    s = CamSettings(thickness_mm=6, tabs=False)
    paths, _ = sheet_toolpaths(sheet_of(pls), parts, CUT, None, s)
    kinds = [p.kind for p in paths]
    # groups in order: marks, then holes, then outside
    order = [k for i, k in enumerate(kinds) if i == 0 or kinds[i - 1] != k]
    assert order == ["mark", "inside", "outside"]
    # nearest neighbour from (0,0): part a (near the origin) first in every group
    for kind in ("mark", "inside", "outside"):
        assert [p.part_id for p in paths if p.kind == kind][0] == "a"
    # each ring's levels are consecutive: 3 levels with the same start, then a new ring
    outs = [p for p in paths if p.kind == "outside"]
    starts = [p.points[0][:2] for p in outs]
    assert starts[0] == starts[1] == starts[2] != starts[3] == starts[4] == starts[5]
    # outside start = ring vertex closest to the sheet origin
    ring0 = ring2d(outs[0])
    assert math.dist(ring0[0], (0, 0)) == pytest.approx(min(math.dist(p, (0, 0)) for p in ring0))


def test_holes_first_false_finishes_each_part():
    parts = {"a": rect_with_hole("a"), "b": rect_with_hole("b")}
    pls = [Placement("b", 0, 300, 200, 0), Placement("a", 0, 10, 10, 0)]
    paths, _ = sheet_toolpaths(sheet_of(pls), parts, CUT, None,
                               CamSettings(thickness_mm=3, tabs=False, holes_first=False))
    seq = [(p.part_id, p.kind) for i, p in enumerate(paths)
           if i == 0 or (paths[i - 1].part_id, paths[i - 1].kind) != (p.part_id, p.kind)]
    assert seq == [("a", "inside"), ("a", "outside"), ("b", "inside"), ("b", "outside")]


# ------------------------------------------------------------ G-code

def _gcode(tmp_path, thickness=3.0, tabs=True, mark_tool=None):
    part = rect_with_hole(marks=[LineString([(20, 10), (80, 10)])])
    s = CamSettings(thickness_mm=thickness, tabs=tabs)
    paths, _ = sheet_toolpaths(sheet_of([Placement("p1", 0, 10, 10, 0)]), {"p1": part}, CUT, mark_tool, s)
    f = tmp_path / "out.nc"
    info = write_ncstudio(str(f), paths, CUT, title="job (test) sheet 1", sheet_w=600, sheet_h=400, safe_z=5)
    return f, info, paths


def test_gcode_structure(tmp_path):
    f, info, _ = _gcode(tmp_path)
    raw = f.read_bytes()
    assert b"\r\n" in raw and raw.count(b"\n") == raw.count(b"\r\n")
    lines = raw.decode().split("\r\n")
    lines = [ln for ln in lines if ln]
    assert lines[0].startswith("(TITLE job [test] sheet 1)")
    assert lines[1].startswith("(TOOL 6 mm flat D6 FLAT")
    assert lines[2].startswith("(SHEET 600 x 400 mm")
    assert lines[3] == "G21 G90 G17"
    assert lines[4] == "G0 Z5"
    assert lines[5] == "M03 S18000"
    assert lines[6] == "G4 P3"
    assert lines[-4:] == ["G0 Z5", "M05", "G0 X0 Y0", "M30"]
    body = "\n".join(lines[3:])
    assert not re.search(r"\bM0?6\b", body) and not re.search(r"\bT\d", body)
    zs = [float(z) for z in re.findall(r"Z(-?\d+(?:\.\d+)?)", body)]
    assert max(zs) == 5
    assert min(zs) == pytest.approx(-3.3)
    assert info.z_min == pytest.approx(-3.3)
    assert info.lines == len(lines)
    assert info.minutes > 0 and info.cut_mm > 0
    # numbers: at most 3 decimals, no trailing zeros
    for num in re.findall(r"[XYZF](-?\d+\.\d+)", body):
        assert len(num.split(".")[1]) <= 3 and not num.endswith("0")
    # first cutting move comes after spindle on
    assert lines.index("M03 S18000") < next(i for i, ln in enumerate(lines) if ln.startswith("G1"))


def test_gcode_no_lift_between_levels_of_same_ring(tmp_path):
    f, _, paths = _gcode(tmp_path, tabs=False)
    lines = [ln for ln in f.read_text().splitlines() if ln]
    body = lines[7:-4]
    rings = sum(1 for i, p in enumerate(paths)
                if p.tool_id == "t6" and p.kind != "mark"
                and (i == 0 or paths[i - 1].points[-1][:2] != p.points[0][:2]))
    marks = sum(1 for p in paths if p.tool_id == "t6" and p.kind == "mark")
    # one rapid travel per ring (or mark), not per level
    assert sum(1 for ln in body if ln.startswith("G0 X")) == rings + marks
    assert sum(1 for ln in body if ln.startswith("G0 Z")) == rings + marks - 1
    # the second level goes straight down at plunge feed
    assert "G1 Z-3.3 F800" in body


def test_gcode_only_writes_its_tool(tmp_path):
    f, info, paths = _gcode(tmp_path, mark_tool=VBIT)
    text = f.read_text()
    assert "Z-0.5" not in text                     # marks belong to the V-bit file
    g = tmp_path / "v.nc"
    vinfo = write_ncstudio(str(g), paths, VBIT, title="v", sheet_w=600, sheet_h=400, safe_z=5)
    assert vinfo.z_min == pytest.approx(-0.5)
    assert "VBIT 60deg" in g.read_text()


def test_gcode_rejects_bad_safe_z(tmp_path):
    with pytest.raises(ValueError):
        write_ncstudio(str(tmp_path / "x.nc"), [], CUT, title="x", sheet_w=1, sheet_h=1, safe_z=0)


# ------------------------------------------------------------ Ruida DXF

def test_ruida_dxf_layers_counts_units(tmp_path):
    parts = {"a": rect_with_hole("a", marks=[LineString([(20, 10), (80, 10)])], layer="score"),
             "b": rect_with_hole("b", marks=[LineString([(20, 40), (80, 40)])], layer="engrave")}
    sheet = sheet_of([Placement("a", 0, 10, 10, 0), Placement("b", 0, 200, 10, 90)])
    f = tmp_path / "s.dxf"
    n = write_ruida_dxf(str(f), sheet, parts, include_sheet_outline=True)
    assert n == 2 * 2 + 2 + 1
    doc = ezdxf.readfile(str(f))
    assert doc.dxfversion == "AC1024"              # R2010
    assert doc.header["$INSUNITS"] == 4
    colours = {ly.dxf.name: ly.dxf.color for ly in doc.layers}
    assert colours["CUT"] == 1 and colours["SCORE"] == 5 and colours["ENGRAVE"] == 7 and colours["SHEET"] == 8
    ents = list(doc.modelspace())
    assert all(e.dxftype() == "LWPOLYLINE" and e.dxf.color == 256 for e in ents)
    by = lambda layer: [e for e in ents if e.dxf.layer == layer]
    assert len(by("CUT")) == 4 and all(e.closed for e in by("CUT"))
    assert len(by("SCORE")) == 1 and not by("SCORE")[0].closed
    assert len(by("ENGRAVE")) == 1 and not by("ENGRAVE")[0].closed
    assert len(by("SHEET")) == 1


def test_ruida_kerf_offsets(tmp_path):
    parts = {"a": rect_with_hole("a")}
    sheet = sheet_of([Placement("a", 0, 10, 10, 0)])
    f = tmp_path / "k.dxf"
    n = write_ruida_dxf(str(f), sheet, parts, kerf_mm=0.2)
    assert n == 2
    doc = ezdxf.readfile(str(f))
    assert "SHEET" not in [ly.dxf.name for ly in doc.layers]
    polys = [Polygon([(p[0], p[1]) for p in e.get_points()]) for e in doc.modelspace()]
    outer, hole = sorted(polys, key=lambda p: -p.area)
    assert outer.bounds == pytest.approx((9.9, 9.9, 110.1, 60.1), abs=1e-6)
    assert math.sqrt(hole.area / math.pi) == pytest.approx(9.9, abs=0.02)


def test_gcode_tab_steps_down_at_plunge_feed(tmp_path):
    f, _, _ = _gcode(tmp_path, tabs=True)
    lines = f.read_text().splitlines()
    # last-level plunge of the hole and of the outside + one step down at the
    # end of each of the 2 tabs
    assert lines.count("G1 Z-3.3 F800") == 4
