"""Tests for nesting/engine.py. Parts are built directly with shapely."""
from __future__ import annotations

import random
import time

import pytest
from shapely import affinity
from shapely.geometry import Point, Polygon, box

from core.models import NestSettings, Part, SheetSpec, place
from nesting.engine import nest, validate


# --------------------------------------------------------------------------- helpers

def norm(g) -> Polygon:
    b = g.bounds
    return affinity.translate(g, -b[0], -b[1])


def rect(pid, w, h, qty=1):
    return Part(id=pid, name=pid, shape=norm(box(0, 0, w, h)), qty=qty)


def ell(pid, a, b, t, qty=1):
    g = Polygon([(0, 0), (a, 0), (a, t), (t, t), (t, b), (0, b)])
    return Part(id=pid, name=pid, shape=norm(g), qty=qty)


def circle(pid, d, qty=1):
    return Part(id=pid, name=pid, shape=norm(Point(0, 0).buffer(d / 2, quad_segs=16)), qty=qty)


def ring(pid, d_out, d_in, qty=1):
    g = Point(0, 0).buffer(d_out / 2, quad_segs=32).difference(Point(0, 0).buffer(d_in / 2, quad_segs=32))
    return Part(id=pid, name=pid, shape=norm(g), qty=qty)


def fast(**kw) -> NestSettings:
    kw.setdefault("time_limit_s", 0.0)
    return NestSettings(**kw)


def all_placements(res):
    return [(s.index, p.part_id, p.copy, p.x, p.y, p.rotation) for s in res.sheets for p in s.placements]


def placed_polys(res, parts, sheet_index=0):
    by = {p.id: p for p in parts}
    return {(p.part_id, p.copy): place(by[p.part_id].shape, p.rotation, p.x, p.y)
            for p in res.sheets[sheet_index].placements}


def mixed_parts(n_types=20, qty=3, seed=7):
    rng = random.Random(seed)
    out = []
    for k in range(n_types):
        a, b = rng.uniform(50, 400), rng.uniform(50, 400)
        t = k % 4
        if t == 0:
            out.append(rect(f"r{k}", a, b, qty))
        elif t == 1:
            out.append(ell(f"l{k}", a, b, 0.3 * min(a, b), qty))
        elif t == 2:
            out.append(circle(f"c{k}", a, qty))
        else:
            out.append(ring(f"o{k}", max(a, 80), max(a, 80) - 50, qty))
    return out


SHEET = SheetSpec(id="std", width=2440, height=1220, margin=10)


# --------------------------------------------------------------------------- tests

def test_mixed_layout_valid_and_margin():
    parts = [rect("a", 300, 200, 3), ell("b", 250, 180, 60, 2), circle("c", 150, 4), ring("d", 400, 300, 1)]
    st = fast(gap_mm=6)
    res = nest(parts, [SHEET], st)
    assert validate(res, parts, [SHEET], st) == []
    assert not res.unplaced
    assert len(res.sheets) == 1
    for g in placed_polys(res, parts).values():
        minx, miny, maxx, maxy = g.bounds
        assert minx >= 10 - 0.05 and miny >= 10 - 0.05
        assert maxx <= 2430 + 0.05 and maxy <= 1210 + 0.05


def test_gap_respected_between_parts():
    parts = [rect("a", 200, 100, 10)]
    st = fast(gap_mm=8)
    res = nest(parts, [SHEET], st)
    assert validate(res, parts, [SHEET], st) == []
    polys = list(placed_polys(res, parts).values())
    dmin = min(polys[i].distance(polys[j]) for i in range(len(polys)) for j in range(i + 1, len(polys)))
    assert dmin >= 8 - 0.05
    # packing should be tight-ish: well under 2 raster cells of extra space
    assert dmin < 8 + 4


def test_big_margin_respected():
    parts = [rect("a", 100, 100, 6)]
    sheet = SheetSpec(id="m", width=600, height=400, margin=40)
    st = fast()
    res = nest(parts, [sheet], st)
    assert validate(res, parts, [sheet], st) == []
    for g in placed_polys(res, parts).values():
        minx, miny, maxx, maxy = g.bounds
        assert minx >= 40 - 0.05 and miny >= 40 - 0.05 and maxx <= 560 + 0.05 and maxy <= 360 + 0.05


def test_part_in_hole_only_when_allowed():
    big = ring("ring", 500, 420)
    small = rect("small", 150, 150)
    sheet = SheetSpec(id="s", width=540, height=540, margin=10)   # only room for the ring
    parts = [big, small]

    st = fast(allow_in_holes=True)
    res = nest(parts, [sheet], st)
    assert validate(res, parts, [sheet], st) == []
    assert not res.unplaced and len(res.sheets) == 1
    polys = placed_polys(res, parts)
    hole = Polygon(polys[("ring", 0)].interiors[0])
    assert hole.contains(polys[("small", 0)])

    st = fast(allow_in_holes=False)
    res = nest(parts, [sheet], st)
    assert validate(res, parts, [sheet], st) == []
    # the small part may not go in the hole, so it needs a second sheet
    assert len(res.sheets) == 2
    assert {p.part_id for p in res.sheets[0].placements} == {"ring"}
    assert {p.part_id for p in res.sheets[1].placements} == {"small"}


def test_too_big_part_unplaced():
    parts = [rect("huge", 3000, 1500), rect("ok", 200, 200, 2)]
    st = fast()
    res = nest(parts, [SHEET], st)
    assert res.unplaced == {"huge": 1}
    assert sum(len(s.placements) for s in res.sheets) == 2
    assert validate(res, parts, [SHEET], st) == []


def test_only_too_big_parts_gives_empty_result():
    parts = [rect("huge", 3000, 1500, 2)]
    res = nest(parts, [SHEET], fast())
    assert res.sheets == [] and res.unplaced == {"huge": 2} and res.utilisation == 0.0


def test_qty_zero_skipped():
    parts = [rect("a", 100, 100, 0), rect("b", 100, 100, 1)]
    res = nest(parts, [SHEET], fast())
    assert [p.part_id for p in res.sheets[0].placements] == ["b"]
    assert not res.unplaced


def test_deterministic_same_seed():
    parts = mixed_parts(n_types=10, qty=2, seed=3)
    st = NestSettings(time_limit_s=0.6, seed=5)
    # the time limit only decides how many attempts run, so compare with a
    # fixed attempt budget: time_limit_s=0 twice, and a longer run twice
    a = nest(parts, [SHEET], fast(seed=5))
    b = nest(parts, [SHEET], fast(seed=5))
    assert all_placements(a) == all_placements(b)
    c = nest(parts, [SHEET], st)
    assert validate(c, parts, [SHEET], st) == []


def test_multi_sheet_when_parts_exceed_one_sheet():
    sheet = SheetSpec(id="small", width=1000, height=600, margin=10)
    parts = [rect("a", 400, 250, 9)]
    st = fast()
    res = nest(parts, [sheet], st)
    assert not res.unplaced
    assert len(res.sheets) >= 2
    assert [s.index for s in res.sheets] == list(range(len(res.sheets)))
    assert sum(len(s.placements) for s in res.sheets) == 9
    assert validate(res, parts, [sheet], st) == []


def test_limited_sheet_qty_leaves_unplaced():
    sheet = SheetSpec(id="small", width=1000, height=600, margin=10, qty=1)
    parts = [rect("a", 400, 250, 9)]
    st = fast()
    res = nest(parts, [sheet], st)
    assert len(res.sheets) == 1
    assert res.unplaced["a"] == 9 - len(res.sheets[0].placements)
    assert validate(res, parts, [sheet], st) == []


def test_offcut_sheet_used_first():
    offcut = SheetSpec(id="off1", width=700, height=500, margin=5, qty=1, is_offcut=True)
    parts = [rect("a", 300, 200, 6)]
    st = fast(rotations=[0, 180])
    res = nest(parts, [offcut, SHEET], st)
    assert res.sheets[0].sheet_id == "off1" and res.sheets[0].is_offcut
    assert len(res.sheets[0].placements) == 4          # 2 x 2 fit on 690 x 490
    assert res.sheets[1].sheet_id == "std" and not res.sheets[1].is_offcut
    assert len(res.sheets[1].placements) == 2
    assert validate(res, parts, [offcut, SHEET], st) == []


def test_non_rectangular_offcut_outline():
    # L-shaped offcut: 800 x 600 with the top-right 500 x 350 corner missing
    outline = Polygon([(0, 0), (800, 0), (800, 250), (300, 250), (300, 600), (0, 600)])
    offcut = SheetSpec(id="offL", width=800, height=600, margin=5, qty=1, outline=outline, is_offcut=True)
    parts = [rect("a", 200, 200, 5)]
    st = fast()
    res = nest(parts, [offcut], st)
    assert validate(res, parts, [offcut], st) == []
    inner = outline.buffer(-5, join_style="mitre").buffer(0.05)
    for g in placed_polys(res, parts).values():
        assert inner.covers(g)
    # 3 along the bottom arm, 1 up the left arm -> 4 fit; the 5th has no room
    assert len(res.sheets[0].placements) == 4 and res.unplaced == {"a": 1}


def test_utilisation_numbers():
    parts = mixed_parts(n_types=8, qty=2, seed=11)
    st = fast()
    res = nest(parts, [SHEET], st)
    by = {p.id: p for p in parts}
    total_used = total_area = 0.0
    for s in res.sheets:
        used = sum(by[p.part_id].area for p in s.placements)
        assert s.used_area == pytest.approx(used)
        assert 0.0 < s.utilisation <= 1.0
        assert s.utilisation == pytest.approx(used / (2440 * 1220))
        total_used += used
        total_area += 2440 * 1220
    assert res.utilisation == pytest.approx(total_used / total_area)
    assert 0.0 < res.utilisation <= 1.0
    assert res.attempts >= 1 and res.elapsed_s >= 0


def test_offcut_strip_reported_when_few_parts():
    parts = [rect("a", 300, 300, 2)]
    st = fast(min_offcut_mm=150)
    res = nest(parts, [SHEET], st)
    off = res.sheets[0].offcut
    assert off is not None
    minx, miny, maxx, maxy = off.bounds
    polys = placed_polys(res, parts).values()
    right = max(g.bounds[2] for g in polys)
    assert minx == pytest.approx(right + st.gap_mm, abs=0.1)
    assert maxx == pytest.approx(2430) and miny == pytest.approx(10) and maxy == pytest.approx(1210)
    assert all(not off.intersects(g.buffer(st.gap_mm - 0.1)) for g in polys)


def test_no_offcut_when_sheet_is_full():
    sheet = SheetSpec(id="s", width=640, height=320, margin=10)
    parts = [rect("a", 300, 300, 2)]
    res = nest(parts, [sheet], fast())
    assert len(res.sheets) == 1 and res.sheets[0].offcut is None


def test_time_limit_zero_still_returns_result():
    parts = mixed_parts(n_types=6, qty=2)
    st = fast(time_limit_s=0)
    res = nest(parts, [SHEET], st)
    assert res.attempts == 1
    assert sum(len(s.placements) for s in res.sheets) == 12
    assert validate(res, parts, [SHEET], st) == []


def test_improvement_loop_runs_and_stays_valid():
    parts = mixed_parts(n_types=6, qty=2, seed=2)
    st = NestSettings(time_limit_s=1.0, seed=3)
    res = nest(parts, [SHEET], st)
    assert res.attempts >= 2
    assert validate(res, parts, [SHEET], st) == []


def test_rotation_used_when_part_only_fits_rotated():
    sheet = SheetSpec(id="tall", width=400, height=1200, margin=10)
    parts = [rect("long", 1000, 100)]
    st = fast(rotations=[0, 90])
    res = nest(parts, [sheet], st)
    assert not res.unplaced
    assert res.sheets[0].placements[0].rotation == 90
    assert validate(res, parts, [sheet], st) == []
    res0 = nest(parts, [sheet], fast(rotations=[0]))
    assert res0.unplaced == {"long": 1}


def test_validate_reports_problems():
    parts = [rect("a", 100, 100, 2)]
    st = fast()
    res = nest(parts, [SHEET], st)
    assert validate(res, parts, [SHEET], st) == []
    # push the second copy onto the first -> overlap
    p0, p1 = res.sheets[0].placements
    p1.x, p1.y, p1.rotation = p0.x + 50, p0.y, p0.rotation
    probs = validate(res, parts, [SHEET], st)
    assert any("overlap" in m for m in probs)
    # move it off the sheet -> margin problem
    p1.x, p1.y = -20, 0
    assert any("edge" in m for m in validate(res, parts, [SHEET], st))
    # too many copies
    parts[0].qty = 1
    assert any("only 1 are needed" in m for m in validate(res, parts, [SHEET], st))
    parts[0].qty = 2
    # wrong sheet index
    res.sheets[0].index = 3
    assert any("index" in m for m in validate(res, parts, [SHEET], st))


def test_validate_reports_small_gap():
    parts = [rect("a", 100, 100, 2)]
    st = fast(gap_mm=6)
    res = nest(parts, [SHEET], st)
    p0, p1 = res.sheets[0].placements
    p1.x, p1.y, p1.rotation = p0.x + 103, p0.y, 0.0
    p0.rotation = 0.0
    probs = validate(res, parts, [SHEET], st)
    assert any("apart" in m for m in probs)


def test_zero_gap_parts_may_touch():
    # 4 x 2 squares of 100 mm; 2 mm (one raster cell) of slack each way
    sheet = SheetSpec(id="s", width=422, height=222, margin=10)
    parts = [rect("a", 100, 100, 8)]
    st = fast(gap_mm=0)
    res = nest(parts, [sheet], st)
    assert not res.unplaced and len(res.sheets) == 1
    assert validate(res, parts, [sheet], st) == []


def test_benchmark_60_mixed_parts_first_attempt():
    """60 instances, 50-400 mm, on 2440 x 1220 at 2 mm. PLAN target < 5 s; CI bound 20 s."""
    parts = mixed_parts(n_types=20, qty=3, seed=7)
    st = fast(resolution_mm=2.0)
    t = time.perf_counter()
    res = nest(parts, [SHEET], st)
    dt = time.perf_counter() - t
    print(f"\nbenchmark: {dt:.2f} s, sheets={len(res.sheets)}, utilisation={res.utilisation:.3f}, "
          f"unplaced={res.unplaced}")
    assert dt < 20.0
    assert sum(len(s.placements) for s in res.sheets) + sum(res.unplaced.values()) == 60
    assert not res.unplaced
    assert validate(res, parts, [SHEET], st) == []


def test_part_far_bigger_than_sheet_is_not_rasterised():
    # a drawing read in the wrong units (inch as mm) can be 60 m long; its grid alone
    # used to take ~8 GB. It must simply go to unplaced, quickly.
    import time as _time
    from shapely.geometry import box as _box
    from core.models import NestSettings as _NS, Part as _Part, SheetSpec as _Sheet
    from nesting.engine import nest as _nest
    big = _Part("big", "big", _box(0, 0, 62000, 31000))
    small = _Part("s", "small", _box(0, 0, 100, 100))
    t0 = _time.perf_counter()
    r = _nest([big, small], [_Sheet("sheet", 2440, 1220)], _NS(time_limit_s=0))
    assert r.unplaced == {"big": 1}
    assert len(r.sheets) == 1 and len(r.sheets[0].placements) == 1
    assert _time.perf_counter() - t0 < 2.0
