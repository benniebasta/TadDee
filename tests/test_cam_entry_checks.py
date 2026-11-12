"""Part 2 CAM tests: ramp / helix entry, lead-in choice, G-code info header,
pre-export checks (2.2, 2.6, 3.3)."""
import math

import ezdxf
import pytest
from shapely.geometry import Point, Polygon

from cam import CamSettings, CamTool, sheet_toolpaths, write_ncstudio, write_ruida_dxf
from cam.checks import pre_export_checks
from core.models import Part, Placement, SheetResult, placed_part

RAMP = CamTool("t6", "6 mm flat", 6.0, pass_depth_mm=3.0, ramp_mm=12.0)
PLUNGE = CamTool("t6", "6 mm flat", 6.0, pass_depth_mm=3.0, ramp_mm=0.0)
TAN10 = math.tan(math.radians(10))


def plate(pid="p1", w=100.0, h=50.0, hole_d=None):
    holes = []
    if hole_d:
        holes = [list(Point(w / 2, h / 2).buffer(hole_d / 2, quad_segs=16).exterior.coords)]
    return Part(pid, f"part-{pid}", Polygon([(0, 0), (w, 0), (w, h), (0, h)], holes))


def sheet_of(placements, w=600.0, h=400.0):
    return SheetResult("s1", 0, w, h, placements, used_area=0.0, utilisation=0.0)


def entry_points(tp, level):
    """Points before the bit first reaches `level` (the entry move)."""
    out = []
    for p in tp.points:
        out.append(p)
        if p[2] <= level + 1e-9:
            break
    return out


# ---------------------------------------------------------------- ramp

def test_ramp_goes_down_gradually_and_never_below_level():
    s = CamSettings(thickness_mm=6, cut_through_mm=0.3, tabs=False)
    paths, _ = sheet_toolpaths(sheet_of([Placement("p1", 0, 20, 30, 0)]), {"p1": plate()}, RAMP, None, s)
    outs = [p for p in paths if p.kind == "outside"]
    levels = [-2.1, -4.2, -6.3]
    assert len(outs) == 3
    z_prev = 0.0
    for tp, level in zip(outs, levels):
        zs = [p[2] for p in tp.points]
        assert min(zs) == pytest.approx(level) and max(zs) == pytest.approx(z_prev)
        assert all(z >= level - 1e-9 for z in zs)
        ent = entry_points(tp, level)
        assert len(ent) > 3                                       # a real ramp, not a plunge
        assert ent[0][2] == pytest.approx(z_prev) and ent[-1][2] == pytest.approx(level)
        assert ent[0][:2] == pytest.approx(ent[-1][:2])           # zigzag ends where it started
        for a, b in zip(ent, ent[1:]):
            dxy = math.hypot(b[0] - a[0], b[1] - a[1])
            assert b[2] <= a[2] + 1e-9                            # only ever goes down
            assert dxy > 1e-6                                     # no vertical plunge inside the ramp
            assert (a[2] - b[2]) / dxy <= TAN10 + 1e-6            # never steeper than the max angle
        # the zigzag footprint stays within ramp_mm / 2 of the start
        assert max(math.dist(p[:2], ent[0][:2]) for p in ent) <= 6.0 + 1e-6
        assert tp.points[-1][:2] == pytest.approx(tp.points[0][:2])
        z_prev = level


def test_ramp_zero_is_a_straight_plunge_as_before():
    s = CamSettings(thickness_mm=6, tabs=False)
    sheet = sheet_of([Placement("p1", 0, 20, 30, 0)])
    paths, _ = sheet_toolpaths(sheet, {"p1": plate(hole_d=20)}, PLUNGE, None, s)
    for tp in paths:
        zs = {round(p[2], 6) for p in tp.points}
        assert len(zs) == 1                                       # one Z per level, no ramp


def test_helix_for_small_hole_stays_inside_the_hole():
    part = plate(hole_d=12)                     # ring of 6 mm diameter: too short for a 12 mm ramp
    pl = Placement("p1", 0, 20, 30, 0)
    s = CamSettings(thickness_mm=6, cut_through_mm=0.3, tabs=False)
    paths, warns = sheet_toolpaths(sheet_of([pl]), {"p1": part}, RAMP, None, s)
    assert warns == []
    shape, _ = placed_part(part, pl)
    hole = Polygon(shape.interiors[0].coords)
    ins = [p for p in paths if p.kind == "inside"]
    assert len(ins) == 3
    z_prev = 0.0
    for tp in ins:
        level = min(p[2] for p in tp.points)
        ent = entry_points(tp, level)
        assert ent[0][2] == pytest.approx(z_prev)
        # helix: descends continuously around the ring, at least one full turn
        turn = sum(math.dist(a[:2], b[:2]) for a, b in zip(ent, ent[1:]))
        assert turn >= math.pi * 6 * 0.95
        for a, b in zip(ent, ent[1:]):
            assert b[2] <= a[2] + 1e-9
        for x, y, z in tp.points:
            assert z >= level - 1e-9
            # bit centre inside the hole by the radius: the bit never touches the wall
            assert hole.contains(Point(x, y))
            assert hole.exterior.distance(Point(x, y)) >= 3.0 - 0.05
        z_prev = level


def test_ramp_never_runs_through_a_tab():
    s = CamSettings(thickness_mm=6, cut_through_mm=0.3, tab_width_mm=6, tab_height_mm=1.5,
                    lead_in="longest_edge_mid")
    paths, _ = sheet_toolpaths(sheet_of([Placement("p1", 0, 20, 30, 0)]), {"p1": plate(200, 120)},
                               RAMP, None, s)
    tab_z = -6 + 1.5
    tabbed = [p for p in paths if p.kind == "outside" and any(abs(q[2] - tab_z) < 1e-9 for q in p.points)]
    assert tabbed                                                 # tabs are there
    for tp in tabbed:
        level = min(q[2] for q in tp.points)
        ent = entry_points(tp, level)
        assert len(ent) >= 3 and ent[1][:2] != pytest.approx(ent[0][:2])   # ramp, not plunge
        tab_pts = [q for q in tp.points if abs(q[2] - tab_z) < 1e-9]
        for e in ent:
            assert min(math.dist(e[:2], t[:2]) for t in tab_pts) >= 0.4
        # tab Z is still never cut into
        lifted = [q for q in tp.points[len(ent):] if q[2] > level + 1e-9]
        assert all(abs(q[2] - tab_z) < 1e-9 for q in lifted)


def test_lead_in_longest_edge_mid():
    s = CamSettings(thickness_mm=3, tabs=False, lead_in="longest_edge_mid")
    paths, _ = sheet_toolpaths(sheet_of([Placement("p1", 0, 20, 30, 0)]), {"p1": plate()}, PLUNGE, None, s)
    out = next(p for p in paths if p.kind == "outside")
    x, y, _ = out.points[0]
    # the long edges (100 mm) of the 3 mm offset ring are y = 27 and y = 83, middle x = 70
    assert x == pytest.approx(70.0) and (y == pytest.approx(27.0) or y == pytest.approx(83.0))
    s_auto = CamSettings(thickness_mm=3, tabs=False)
    p2, _ = sheet_toolpaths(sheet_of([Placement("p1", 0, 20, 30, 0)]), {"p1": plate()}, PLUNGE, None, s_auto)
    assert next(p for p in p2 if p.kind == "outside").points[0][:2] != pytest.approx((70.0, 27.0))
    with pytest.raises(ValueError):
        sheet_toolpaths(sheet_of([]), {}, PLUNGE, None, CamSettings(thickness_mm=3, lead_in="corner"))


def test_time_estimate_includes_ramp_moves(tmp_path):
    s = CamSettings(thickness_mm=6, tabs=False)
    sheet = sheet_of([Placement("p1", 0, 20, 30, 0)])
    a, _ = sheet_toolpaths(sheet, {"p1": plate(hole_d=12)}, PLUNGE, None, s)
    b, _ = sheet_toolpaths(sheet, {"p1": plate(hole_d=12)}, RAMP, None, s)
    ia = write_ncstudio(str(tmp_path / "a.nc"), a, PLUNGE, title="a", sheet_w=600, sheet_h=400, safe_z=5)
    ib = write_ncstudio(str(tmp_path / "b.nc"), b, RAMP, title="b", sheet_w=600, sheet_h=400, safe_z=5)
    assert ib.cut_mm > ia.cut_mm and ib.minutes > ia.minutes
    assert ib.z_min == pytest.approx(ia.z_min) == pytest.approx(-6.3)
    text = (tmp_path / "b.nc").read_bytes().decode()
    assert "\r\n" in text and "M06" not in text and text.rstrip().endswith("M30")
    # one plunge from safe Z per ring (to the material top), then ramps
    assert text.count("G1 Z0 F800") == 2


def test_gcode_info_header(tmp_path):
    s = CamSettings(thickness_mm=3, tabs=False)
    paths, _ = sheet_toolpaths(sheet_of([Placement("p1", 0, 20, 30, 0)]), {"p1": plate()}, RAMP, None, s)
    f = tmp_path / "h.nc"
    write_ncstudio(str(f), paths, RAMP, title="t", sheet_w=600, sheet_h=400, safe_z=5,
                   info=["JOB Shop (front) - ID 7", "SHEET 1 OF 2 - REVISION 3"])
    lines = f.read_text().splitlines()
    assert lines[3] == "(JOB Shop [front] - ID 7)" and lines[4] == "(SHEET 1 OF 2 - REVISION 3)"
    assert lines[5] == "G21 G90 G17"


def test_ruida_info_in_header_not_entities(tmp_path):
    sheet = sheet_of([Placement("p1", 0, 20, 30, 0)])
    f = tmp_path / "r.dxf"
    n = write_ruida_dxf(str(f), sheet, {"p1": plate()}, info={"NEST_REVISION": "4", "NEST_JOB": "Sign"})
    doc = ezdxf.readfile(str(f))
    assert dict(doc.header.custom_vars) == {"NEST_REVISION": "4", "NEST_JOB": "Sign"}
    assert len(doc.modelspace()) == n == 1                       # no text entity to burn


# ---------------------------------------------------------------- checks

def slotted(slot_w=3.0):
    """100 x 60 plate with a slot slot_w wide and 30 deep from the top edge."""
    a, b = 50 - slot_w / 2, 50 + slot_w / 2
    return Part("s", "slotted", Polygon([(0, 0), (100, 0), (100, 60), (b, 60), (b, 30), (a, 30), (a, 60), (0, 60)]))


def codes(issues):
    return {i["code"] for i in issues}


def test_checks_catch_3mm_slot_with_6mm_bit():
    sheet = sheet_of([Placement("s", 0, 20, 30, 0)])
    s = CamSettings(thickness_mm=3)
    issues = pre_export_checks([sheet], {"s": slotted()}, target="ncstudio", cut_tool=PLUNGE, cam=s,
                               gap_mm=7, bed_w=2500, bed_h=1300)
    slot = [i for i in issues if i["code"] == "slot_too_narrow"]
    assert len(slot) == 1 and slot[0]["severity"] == "warn" and slot[0]["part_id"] == "s"
    assert slot[0]["sheet"] == 1 and 68 < slot[0]["x"] < 72 and 60 < slot[0]["y"] < 90
    assert "narrower than the 6 mm bit" in slot[0]["message"]
    # a 3 mm bit fits the 3.5 mm slot (only its rounded bottom corners are lost)
    thin = CamTool("t3", "3 mm flat", 3.0, pass_depth_mm=2.0)
    issues = pre_export_checks([sheet], {"s": slotted(3.5)}, target="ncstudio", cut_tool=thin, cam=s, gap_mm=7)
    assert "slot_too_narrow" not in codes(issues)


def test_checks_plain_parts_are_clean():
    sheet = sheet_of([Placement("p1", 0, 20, 30, 0)])
    issues = pre_export_checks([sheet], {"p1": plate(hole_d=20)}, target="ncstudio", cut_tool=PLUNGE,
                               cam=CamSettings(thickness_mm=3), gap_mm=7, bed_w=2500, bed_h=1300)
    assert issues == []                          # 90 degree corners and a round hole are fine


def test_checks_sharp_corner_hole_too_small_small_part():
    notch = Part("n", "notch", Polygon([(0, 0), (100, 0), (100, 80), (50, 5), (0, 80)]))   # 67 deg V notch
    tiny = Part("t", "tiny", Polygon([(0, 0), (30, 0), (30, 30), (0, 30)],
                                     [list(Point(15, 15).buffer(2).exterior.coords)]))
    sheet = sheet_of([Placement("n", 0, 20, 30, 0), Placement("t", 0, 200, 30, 0)])
    issues = pre_export_checks([sheet], {"n": notch, "t": tiny}, target="ncstudio", cut_tool=PLUNGE,
                               cam=CamSettings(thickness_mm=3, tabs=False), gap_mm=7)
    by = {(i["code"], i["part_id"]) for i in issues}
    assert ("corner_too_tight", "n") in by
    assert ("hole_too_small", "t") in by
    assert ("small_part_no_tabs", "t") in by and ("small_part_no_tabs", "n") not in by
    # tabs on: the 30 mm part gets tabs, no loose-part warning
    issues = pre_export_checks([sheet], {"n": notch, "t": tiny}, target="ncstudio", cut_tool=PLUNGE,
                               cam=CamSettings(thickness_mm=3), gap_mm=7)
    assert ("small_part_no_tabs", "t") not in {(i["code"], i["part_id"]) for i in issues}
    # ...but not when it is under the tab rule
    issues = pre_export_checks([sheet], {"n": notch, "t": tiny}, target="ncstudio", cut_tool=PLUNGE,
                               cam=CamSettings(thickness_mm=3, tab_min_part_mm=40), gap_mm=7)
    assert ("small_part_no_tabs", "t") in {(i["code"], i["part_id"]) for i in issues}


def test_checks_blocks_bed_depth_gap():
    sheet = sheet_of([Placement("p1", 0, 20, 30, 0)])
    parts = {"p1": plate()}
    cam = CamSettings(thickness_mm=3)
    out = pre_export_checks([sheet], parts, target="ncstudio", cut_tool=PLUNGE, cam=cam, gap_mm=7,
                            bed_w=100, bed_h=100)
    bed = [i for i in out if i["code"] == "outside_bed"]
    assert bed and bed[0]["severity"] == "block" and bed[0]["x"] > 100
    out = pre_export_checks([sheet], parts, target="ncstudio", cut_tool=PLUNGE,
                            cam=CamSettings(thickness_mm=3, cut_through_mm=2), gap_mm=7)
    assert [i["severity"] for i in out if i["code"] == "too_deep"] == ["block"]
    out = pre_export_checks([sheet], parts, target="ncstudio", cut_tool=PLUNGE, cam=cam, gap_mm=6)
    assert [i["severity"] for i in out if i["code"] == "bit_wider_than_gap"] == ["block"]
    assert out[0]["severity"] == "block"                     # blocks listed first
    out = pre_export_checks([sheet], parts, target="ruida", gap_mm=2, kerf_mm=3, bed_w=100, bed_h=100)
    assert codes(out) == {"kerf_wider_than_gap", "outside_bed"}
