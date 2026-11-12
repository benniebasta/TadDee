"""Part 2 nesting features (PLAN.md Part 2: 2.1, 2.3, 2.4, 2.5, 2.8 engine side):
rotation choices, grain lock, long search with progress and cancel, finer
resolution with a cell cap, sets, mirror, separate colours, irregular remnants."""
from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient
from shapely import affinity
from shapely.geometry import Point, Polygon, box

from cam import CamSettings, CamTool, sheet_toolpaths
from core.models import NestSettings, Part, Placement, SheetResult, SheetSpec, place, placed_part
from nesting.engine import effective_resolution, nest, sheet_container, validate

from app.main import create_app

SHEET = SheetSpec(id="std", width=2440, height=1220, margin=10)


def norm(g):
    b = g.bounds
    return affinity.translate(g, -b[0], -b[1])


def ell(pid, a=200, b=120, t=40, qty=1, **kw):
    return Part(id=pid, name=pid, shape=norm(Polygon([(0, 0), (a, 0), (a, t), (t, t), (t, b), (0, b)])),
                qty=qty, **kw)


def rect(pid, w, h, qty=1, **kw):
    return Part(id=pid, name=pid, shape=box(0, 0, w, h), qty=qty, **kw)


def fast(**kw) -> NestSettings:
    kw.setdefault("time_limit_s", 0.0)
    return NestSettings(**kw)


def placements(res):
    return [p for s in res.sheets for p in s.placements]


# --------------------------------------------------------------------------- rotations

def test_none_rotation_keeps_angle_zero():
    # L parts would pack tighter turned; with "none" every copy stays at 0
    parts = [ell("L", qty=12)]
    st = fast(gap_mm=6, rotations=[0.0])
    res = nest(parts, [SHEET], st)
    assert len(placements(res)) == 12
    assert all(p.rotation == 0.0 for p in placements(res))
    assert res.meta["rotations"] == [0.0]
    assert validate(res, parts, [SHEET], st) == []


def test_free_rotation_uses_10_degree_steps_and_stays_valid():
    rots = [float(a) for a in range(0, 360, 10)]
    parts = [ell("L", 300, 200, 60, qty=3), rect("r", 400, 90, qty=2)]
    sheet = SheetSpec("s", 800, 600, margin=10)
    st = fast(gap_mm=4, rotations=rots, resolution_mm=1.0)
    res = nest(parts, [sheet], st)
    assert res.meta["rotations"] == rots
    assert all(p.rotation in rots for p in placements(res))
    assert validate(res, parts, [sheet], st) == []


# --------------------------------------------------------------------------- resolution cap

def test_fine_resolution_is_capped_on_a_full_sheet():
    assert effective_resolution(0.25, [SHEET], 12_000_000) == 0.5     # 2440 x 1220 at 0.25 = 47.6 M cells
    assert effective_resolution(0.5, [SHEET], 12_000_000) == 0.5
    small = SheetSpec("small", 600, 400)
    assert effective_resolution(0.25, [small], 12_000_000) == 0.25    # 3.8 M cells: allowed
    res = nest([rect("a", 30, 20, qty=4)], [small], fast(gap_mm=2, resolution_mm=0.25))
    assert res.meta["resolution_mm"] == 0.25 and not res.unplaced


# --------------------------------------------------------------------------- sets, mirror, colours

def test_sets_multiply_counts_in_api(client):
    j = new_job(client)
    up = upload_svg(client, j["id"], TWO_SHAPES)
    pid = up["parts"][0]["id"]
    client.patch(f"/api/jobs/{j['id']}/parts/{pid}", json={"qty": 2})
    r = client.post(f"/api/jobs/{j['id']}/nest", json={**FAST, "sets": 3})
    assert r.status_code == 200, r.text
    out = r.json()
    placed = sum(s["parts_count"] for s in out["sheets"]) + sum(u["count"] for u in out["unplaced"])
    assert placed == (2 + 1) * 3
    assert out["meta"]["sets"] == 3
    detail = client.get(f"/api/jobs/{j['id']}").json()
    assert detail["settings"]["sets"] == 3
    assert next(p for p in detail["parts"] if p["id"] == pid)["qty"] == 2      # qty stays per set
    # export does not think quantities changed (it knows the sets)
    tools = client.get("/api/tools").json()
    six = next(t for t in tools if t["diameter_mm"] == 6 and t["kind"] == "flat")
    ex = client.post(f"/api/jobs/{j['id']}/export", json={"target": "ncstudio", "cut_tool_id": six["id"]})
    assert ex.status_code == 200, ex.text
    assert not any("changed after nesting" in w for w in ex.json()["warnings"])


def test_mirrored_part_is_placed_flipped_and_its_toolpath_is_mirrored():
    shape = norm(Polygon([(0, 0), (200, 0), (200, 40), (40, 40), (40, 120), (0, 120)]))
    plain = Part("p", "p", shape, qty=1)
    flipped = Part("m", "m", shape, qty=1, mirror=True)
    st = fast(gap_mm=8, rotations=[0.0])
    res = nest([flipped], [SHEET], st)
    pl = placements(res)[0]
    assert pl.mirror is True and pl.rotation == 0.0
    assert validate(res, [flipped], [SHEET], st) == []
    got, _ = placed_part(flipped, pl)
    want = affinity.translate(affinity.scale(shape, -1, 1, origin=(0, 0)), pl.x, pl.y)
    assert got.symmetric_difference(want).area < 1e-6
    unflipped = place(shape, 0, pl.x - 200, pl.y)       # same box, not mirrored
    assert got.symmetric_difference(unflipped).area > 1000

    # CAM follows the placement: the outside path hugs the mirrored outline
    tool = CamTool("t6", "6 mm flat", 6.0, pass_depth_mm=10.0)
    cs = CamSettings(thickness_mm=3.0, tabs=False)
    sheet = SheetResult("std", 0, 2440, 1220, [pl], used_area=0, utilisation=0)
    paths, _ = sheet_toolpaths(sheet, {"m": flipped}, tool, None, cs)
    outside = [p for p in paths if p.kind == "outside"]
    assert outside
    ring = Polygon([(x, y) for x, y, _ in outside[0].points])
    assert ring.symmetric_difference(want.buffer(3.0)).area < 0.02 * want.buffer(3.0).area
    assert ring.symmetric_difference(unflipped.buffer(3.0)).area > 1000
    # the plain part's path is the other way round
    pl2 = Placement("p", 0, pl.x - 200, pl.y, 0.0)
    paths2, _ = sheet_toolpaths(SheetResult("std", 0, 2440, 1220, [pl2], 0, 0), {"p": plain}, tool, None, cs)
    ring2 = Polygon([(x, y) for x, y, _ in [p for p in paths2 if p.kind == "outside"][0].points])
    assert ring2.symmetric_difference(unflipped.buffer(3.0)).area < 0.02 * unflipped.buffer(3.0).area


def test_separate_colours_never_share_a_sheet():
    red = [rect(f"r{k}", 300, 200, qty=2, color="#ff0000") for k in range(3)]
    blue = [rect(f"b{k}", 250, 150, qty=2, color="#0000ff") for k in range(3)]
    parts = red + blue
    together = nest(parts, [SHEET], fast(gap_mm=6))
    assert len(together.sheets) == 1                      # all fit one sheet when mixed
    st = fast(gap_mm=6, separate_colors=True)
    res = nest(parts, [SHEET], st)
    colour = {p.id: p.color for p in parts}
    assert len(res.sheets) == 2 and not res.unplaced
    for sh in res.sheets:
        assert len({colour[p.part_id] for p in sh.placements}) == 1
    assert validate(res, parts, [SHEET], st) == []
    assert res.meta["separate_colors"] is True


def test_importer_reads_colours():
    from core.importers import import_file

    svg = (b'<svg xmlns="http://www.w3.org/2000/svg" width="200mm" height="100mm" viewBox="0 0 200 100">'
           b'<rect x="0" y="0" width="50" height="50" fill="#FF0000"/>'
           b'<rect x="100" y="0" width="50" height="50" fill="none" stroke="#00ff00"/></svg>')
    parts = import_file(svg, "c.svg").parts
    assert sorted(p.color for p in parts) == ["#00ff00", "#ff0000"]


# --------------------------------------------------------------------------- irregular remnants

def test_irregular_offcut_outline_and_hole_honoured():
    # an L-shaped remnant with a round piece missing in its corner
    outline = Polygon([(0, 0), (900, 0), (900, 300), (300, 300), (300, 700), (0, 700)],
                      [list(Point(150, 150).buffer(80).exterior.coords)])
    off = SheetSpec("off", 900, 700, margin=10, qty=1, outline=outline, is_offcut=True)
    parts = [rect("a", 60, 40, qty=40)]
    st = fast(gap_mm=5, rotations=[0.0, 90.0])
    res = nest(parts, [off], st)
    assert res.sheets and res.sheets[0].sheet_id == "off"
    assert validate(res, parts, [off], st) == []
    inside = sheet_container(off).buffer(0.01)
    hole = Point(150, 150).buffer(80 + 10 - 0.01)          # the hole plus the margin
    by = {p.id: p for p in parts}
    for p in res.sheets[0].placements:
        g, _ = placed_part(by[p.part_id], p)
        assert inside.covers(g)
        assert not g.intersects(hole)
        assert not g.intersects(box(300 - 9.99, 300 - 9.99, 900, 700))   # the cut-away corner


# --------------------------------------------------------------------------- long search, progress, cancel

def _many():
    out = []
    for k in range(25):
        out.append(ell(f"l{k}", 120 + 7 * k, 80 + 5 * k, 30, qty=3))
    return out


def test_cancel_stops_within_a_second_and_keeps_best():
    parts = _many()
    st = NestSettings(gap_mm=6, rotations=[float(a) for a in range(0, 360, 30)], resolution_mm=1.0,
                      time_limit_s=120.0)
    t0 = time.perf_counter()
    stop_at = t0 + 4.0
    seen = []
    res = nest(parts, [SHEET], st, should_stop=lambda: time.perf_counter() > stop_at,
               progress=lambda f, best: seen.append((f, best)))
    late = time.perf_counter() - stop_at
    assert late < 1.0, f"stopped {late:.2f} s after the cancel"
    assert res.meta["stopped"] == "cancel"
    assert validate(res, parts, [SHEET], st) == []
    placed = len(placements(res)) + sum(res.unplaced.values())
    assert placed == 75
    fr = [f for f, _ in seen]
    assert fr == sorted(fr) and fr[-1] == 1.0
    assert seen[-1][1]["sheets"] == len(res.sheets)


def test_cancel_during_first_layout_returns_partial_result():
    parts = _many()
    st = NestSettings(gap_mm=6, rotations=[0.0, 90.0], resolution_mm=1.0, time_limit_s=60.0)
    t0 = time.perf_counter()
    res = nest(parts, [SHEET], st, should_stop=lambda: time.perf_counter() - t0 > 0.3)
    assert time.perf_counter() - t0 < 1.3
    assert res.meta["stopped"] == "cancel" and res.meta["complete"] is False
    assert len(placements(res)) + sum(res.unplaced.values()) == 75
    assert sum(res.unplaced.values()) > 0
    assert validate(res, parts, [SHEET], st) == []


def test_long_run_keeps_best_layout():
    parts = _many()
    quick = nest(parts, [SHEET], fast(gap_mm=6, resolution_mm=3.0))
    st = NestSettings(gap_mm=6, resolution_mm=3.0, time_limit_s=4.0, seed=3)
    t0 = time.perf_counter()
    res = nest(parts, [SHEET], st)
    dt = time.perf_counter() - t0
    assert dt < 4.0 + 2.0                                 # stops on time (one unfinished attempt dropped)
    assert res.attempts > 1 and res.meta["stopped"] in ("time", "exhausted")
    assert validate(res, parts, [SHEET], st) == []
    # never worse than the plain first layout
    key = lambda r: (sum(r.unplaced.values()), len(r.sheets))
    assert key(res) <= key(quick)
    if key(res) == key(quick):
        last = lambda r: max(place(next(p for p in parts if p.id == q.part_id).shape, q.rotation, q.x, q.y)
                             .bounds[2] for q in r.sheets[-1].placements)
        assert last(res) <= last(quick) + 1e-6


# --------------------------------------------------------------------------- API

TWO_SHAPES = b"""<?xml version="1.0"?>
<svg xmlns="http://www.w3.org/2000/svg" width="300mm" height="200mm" viewBox="0 0 300 200">
  <path d="M10 10 H170 V40 H40 V110 H10 Z" fill="#ff0000"/>
  <rect x="200" y="10" width="80" height="50" fill="#0000ff"/>
</svg>"""

REMNANT = b"""<?xml version="1.0"?>
<svg xmlns="http://www.w3.org/2000/svg" width="1000mm" height="800mm" viewBox="0 0 1000 800">
  <path d="M0 0 H900 V300 H300 V700 H0 Z M70 150 A80 80 0 1 0 230 150 A80 80 0 1 0 70 150 Z"
        fill="#ccc" fill-rule="evenodd"/>
  <rect x="950" y="10" width="20" height="20" fill="#ccc"/>
</svg>"""

FAST = {"resolution_mm": 3, "time_limit_s": 0, "seed": 1}


@pytest.fixture
def client(tmp_path):
    app = create_app(str(tmp_path / "data"))
    with TestClient(app) as c:
        c.app_ref = app
        yield c


def new_job(c, material="Acrylic 3 mm", machine="Router (NC Studio)"):
    mat = next(m for m in c.get("/api/materials").json() if m["name"] == material)
    mach = next(m for m in c.get("/api/machines").json() if m["name"] == machine)
    r = c.post("/api/jobs", json={"name": "P2", "material_id": mat["id"], "machine_id": mach["id"]})
    assert r.status_code == 201, r.text
    return r.json()


def upload_svg(c, jid, data, name="p.svg"):
    r = c.post(f"/api/jobs/{jid}/files", files=[("file", (name, data, "image/svg+xml"))])
    assert r.status_code == 200, r.text
    return r.json()


def stored_result(c, jid):
    from app import services as sv

    with c.app_ref.state.db.session() as s:
        return json.loads(sv.latest_nest(s, jid).result_json)["result"]


def test_grain_material_forces_0_180(client):
    j = new_job(client)
    r = client.put(f"/api/materials/{j['material_id']}", json={"grain": True})
    assert r.status_code == 200 and r.json()["grain"] is True
    upload_svg(client, j["id"], TWO_SHAPES)
    r = client.post(f"/api/jobs/{j['id']}/nest", json={**FAST, "rotations": [0, 90, 180, 270]})
    assert r.status_code == 200, r.text
    meta = r.json()["meta"]
    assert meta["grain_locked"] is True
    assert meta["rotations"] == [0.0, 180.0] and meta["rotations_requested"] == [0.0, 90.0, 180.0, 270.0]
    res = stored_result(client, j["id"])
    assert all(p["rotation"] in (0.0, 180.0) for s in res["sheets"] for p in s["placements"])
    # free rotation request on a grain material: still only 0/180
    r = client.post(f"/api/jobs/{j['id']}/nest", json={**FAST, "rotations": list(range(0, 360, 10))})
    assert r.json()["meta"]["rotations"] == [0.0, 180.0]
    # no grain: rotations as asked, not locked
    client.put(f"/api/materials/{j['material_id']}", json={"grain": False})
    r = client.post(f"/api/jobs/{j['id']}/nest", json={**FAST, "rotations": [0, 90]})
    assert r.json()["meta"]["grain_locked"] is False and r.json()["meta"]["rotations"] == [0.0, 90.0]


def test_part_mirror_flag_and_colour_through_api(client):
    j = new_job(client)
    up = upload_svg(client, j["id"], TWO_SHAPES)
    colours = sorted(p["color"] for p in up["parts"])
    assert colours == ["#0000ff", "#ff0000"]
    pid = up["parts"][0]["id"]
    r = client.patch(f"/api/jobs/{j['id']}/parts/{pid}", json={"mirror": True})
    assert r.status_code == 200 and r.json()["mirror"] is True
    r = client.post(f"/api/jobs/{j['id']}/nest", json={**FAST, "separate_colors": True})
    assert r.status_code == 200, r.text
    assert r.json()["sheets_used"] == 2 and r.json()["meta"]["separate_colors"] is True
    res = stored_result(client, j["id"])
    flags = {p["part_id"]: p["mirror"] for s in res["sheets"] for p in s["placements"]}
    assert flags[pid] is True and sum(flags.values()) == 1


def test_time_limit_capped_at_300(client):
    j = new_job(client)
    upload_svg(client, j["id"], TWO_SHAPES)
    r = client.post(f"/api/jobs/{j['id']}/nest", json={"resolution_mm": 3, "time_limit_s": 1000, "seed": 1})
    assert r.status_code == 200
    assert client.get(f"/api/jobs/{j['id']}").json()["settings"]["time_limit_s"] == 300
    assert r.json()["meta"]["time_limit_s"] == 300


def test_offcut_by_size_and_drawn_remnant(client):
    mat = next(m for m in client.get("/api/materials").json() if m["name"] == "Acrylic 3 mm")
    r = client.post("/api/offcuts", json={"material_id": mat["id"], "width": 600, "height": 400})
    assert r.status_code == 201, r.text
    rect_oc = r.json()
    assert rect_oc["width"] == 600 and rect_oc["irregular"] is False and rect_oc["source_job_id"] is None
    assert client.post("/api/offcuts", json={"material_id": 9999, "width": 600, "height": 400}).status_code == 400
    assert client.post("/api/offcuts", json={"material_id": mat["id"], "width": 5, "height": 400}).status_code == 400

    r = client.post("/api/offcuts/upload", data={"material_id": str(mat["id"])},
                    files={"file": ("remnant.svg", REMNANT, "image/svg+xml")})
    assert r.status_code == 201, r.text
    oc = r.json()
    assert oc["irregular"] is True and oc["holes"] == 1 and oc["name"] == "remnant"
    assert abs(oc["width"] - 900) < 0.5 and abs(oc["height"] - 700) < 0.5
    assert any("ignored" in w for w in oc["warnings"])
    bad = client.post("/api/offcuts/upload", data={"material_id": str(mat["id"])},
                      files={"file": ("x.txt", b"hello", "text/plain")})
    assert bad.status_code == 400

    # nest into the remnants: smallest offcut first, every part inside the drawn outline
    j = new_job(client)
    up = upload_svg(client, j["id"], TWO_SHAPES)
    for p in up["parts"]:
        client.patch(f"/api/jobs/{j['id']}/parts/{p['id']}", json={"qty": 6})
    r = client.post(f"/api/jobs/{j['id']}/nest", json={**FAST, "use_offcuts": True})
    assert r.status_code == 200, r.text
    sheets = r.json()["sheets"]
    assert sheets[0]["is_offcut"]
    res = stored_result(client, j["id"])
    from shapely import wkt
    outline = wkt.loads(oc["outline_wkt"])
    from app import services as sv
    with client.app_ref.state.db.session() as s:
        parts = {p.id: p for p in sv.job_parts(s, j["id"])}
    for sh in res["sheets"]:
        if sh["sheet_id"] != f"offcut-{oc['id']}":
            continue
        for p in sh["placements"]:
            g, _ = placed_part(parts[p["part_id"]], Placement(**p))
            assert outline.buffer(-9.9).covers(g)


def test_old_database_gets_new_columns(tmp_path):
    import sqlite3

    from app.db import Database
    from app.models import Material

    d = tmp_path / "old"
    d.mkdir()
    con = sqlite3.connect(d / "nest.db")
    con.execute("CREATE TABLE materials (id INTEGER PRIMARY KEY, name VARCHAR(200), thickness_mm FLOAT, "
                "sheet_w FLOAT, sheet_h FLOAT, price_per_sheet FLOAT, margin_mm FLOAT)")
    con.execute("INSERT INTO materials VALUES (1, 'Old', 3, 2440, 1220, 0, 10)")
    con.commit()
    con.close()
    db = Database(str(d))
    db.create_all()
    with db.session() as s:
        assert s.get(Material, 1).grain is False
    db.dispose()
